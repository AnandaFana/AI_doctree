"""Durable, local governance for an explicitly scanned document tree.

The scanner owns source discovery and source versions. This module owns deliveries,
summary review, invalidation, and audit history. It never edits or executes a source
file. Call ``refresh`` with a fresh scan before an external mutation to detect new
files as well as edits to existing evidence.
"""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import stat
import tempfile
import time
from typing import Any


FLAG_KEYS = ("blocked", "unverified", "decisions")
STAGE_KEYS = ("delivery", "integration", "execution", "acceptance")


class ConflictError(ValueError):
    """The caller's source version or summary inputs are no longer current."""


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _strings(value: Any, name: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"{name} must be a list of strings")
    return list(dict.fromkeys(item for item in value if item.strip()))


def _flags(value: Any) -> dict[str, list[str]]:
    if not isinstance(value, dict):
        raise ValueError("flags must be an object")
    return {key: _strings(value.get(key, []), f"flags.{key}") for key in FLAG_KEYS}


def _stages(value: Any, *, partial: bool = False) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) - set(STAGE_KEYS):
        raise ValueError("stages may only contain delivery, integration, execution, acceptance")
    for key, stage in value.items():
        if not isinstance(stage, str) or not stage.strip():
            raise ValueError(f"stages.{key} must be a non-empty string")
    return dict(value) if partial else {key: value.get(key, "unknown") for key in STAGE_KEYS}


def _relative_path(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("evidence.path must be a non-empty project-relative path")
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if (path.is_absolute() or PureWindowsPath(value).drive or ".." in path.parts or ':' in normalized
            or any(part.casefold() in {'.git', '.doctree'} for part in path.parts)):
        raise ValueError(f"evidence path must stay inside its project: {value}")
    if str(path) in ("", "."):
        raise ValueError("evidence path must identify a file")
    return str(path)


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_evidence_location(root: Path, relative: str) -> Path:
    target = root / _relative_path(relative)
    for component in (*reversed(target.parents), target):
        try:
            info = component.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError(f"evidence path traverses a symbolic link or junction: {relative}")
    try:
        target.resolve().relative_to(root.resolve())
    except ValueError as error:
        raise ValueError(f"evidence resolves outside its project: {relative}") from error
    return target


class StateStore:
    """A single atomic JSON snapshot guarded by an OS interprocess lock.

    Every transaction reloads the snapshot under the lock. Two workers updating
    different leaves therefore preserve both writes. Audit events are part of the
    same atomic replacement as the corresponding state mutation.
    """

    def __init__(self, path: str | os.PathLike[str], *, lock_timeout: float = 20.0):
        self.path = Path(path)
        self.lock_path = self.path.with_name(self.path.name + ".lock")
        self.lock_timeout = lock_timeout

    @contextmanager
    def _locked(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+b") as lock:
            lock.seek(0, os.SEEK_END)
            if lock.tell() == 0:
                lock.write(b"\0")
                lock.flush()
            deadline = time.monotonic() + self.lock_timeout
            while True:
                try:
                    lock.seek(0)
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError as error:
                    if time.monotonic() >= deadline:
                        raise ConflictError("state store is busy; retry after the other transaction") from error
                    time.sleep(0.05)
            try:
                yield
            finally:
                lock.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _empty() -> dict[str, Any]:
        return {
            "schema": 1, "generation": 0, "scan_id": "", "scanned_at_ns": 0, "projects": [],
            "nodes": {}, "governance": {}, "warnings": [], "scan_policy": {},
            "events": [], "deliveries": {}, "commits": {}, "viewed_generation": 0,
        }

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return self._empty()
        try:
            state = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ValueError(f"cannot read state snapshot: {self.path}") from error
        if not isinstance(state, dict) or state.get("schema") != 1:
            raise ValueError("unsupported governance state schema")
        state.setdefault("scanned_at_ns", 0)
        for key in self._empty():
            if key not in state:
                raise ValueError(f"incomplete governance state: missing {key}")
        for node_id, meta in state["governance"].items():
            meta.setdefault("summary_state", "stale" if meta["pending"] else (
                "reviewed_current" if meta["review"] == "reviewed" else "source_candidate"))
            if "delivery_evidence" not in meta:
                # Migrate old snapshots from immutable imported evidence rather
                # than silently adopting bytes that may already have changed.
                task_id = meta.get("current_delivery")
                refs = state["deliveries"].get(task_id, {}).get("delivery", {}).get("evidence", [])
                observed = self._observe_delivery_evidence(state, state["nodes"][node_id], refs)
                for ref in refs:
                    item = observed[_relative_path(ref["path"])]
                    item["task_ids"] = [task_id]
                    if item["sha256"] != ref.get("sha256"):
                        item.update(sha256=ref.get("sha256"), content_version=ref.get("sha256"), status="recorded")
                meta["delivery_evidence"] = observed
        return state

    def _save(self, state: dict[str, Any]) -> None:
        temporary: str | None = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                             prefix=self.path.name + ".", suffix=".tmp",
                                             dir=self.path.parent, delete=False) as output:
                temporary = output.name
                json.dump(state, output, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)
            temporary = None
            if os.name != "nt":
                descriptor = os.open(self.path.parent, os.O_RDONLY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
        finally:
            if temporary is not None:
                Path(temporary).unlink(missing_ok=True)

    @staticmethod
    def _event(state: dict, kind: str, **details: Any) -> None:
        state["generation"] += 1
        state["events"].append({
            "sequence": state["generation"], "kind": kind,
            "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **details,
        })

    @staticmethod
    def _node(state: dict, node_id: str) -> dict:
        if node_id not in state["nodes"]:
            raise ValueError(f"unknown node: {node_id}")
        return state["nodes"][node_id]

    @staticmethod
    def _ancestors(nodes: dict, node_id: str) -> list[str]:
        result: list[str] = []
        parent = nodes.get(node_id, {}).get("parent")
        while parent is not None:
            if parent in result:
                raise ValueError("tree contains a parent cycle")
            result.append(parent)
            parent = nodes[parent].get("parent")
        return result

    @classmethod
    def _validate_index(cls, index: Any) -> dict:
        if not isinstance(index, dict) or index.get("schema") != 1:
            raise ValueError("index schema must be 1")
        if not isinstance(index.get("scan_id"), str) or not index["scan_id"]:
            raise ValueError("index requires a non-empty scan_id")
        if not isinstance(index.get("projects"), list) or not isinstance(index.get("nodes"), dict):
            raise ValueError("index requires projects and nodes")
        if index.get("complete") is False or any(
                project.get("complete") is False or project.get("stats", {}).get("bounded")
                or project.get("stats", {}).get("complete") is False for project in index["projects"]):
            raise ValueError("incomplete or partial scan cannot replace the last complete governance snapshot")
        if not isinstance(index.get("scanned_at_ns", 0), int) or index.get("scanned_at_ns", 0) < 0:
            raise ValueError("scanned_at_ns must be a nonnegative integer captured before discovery")
        result = deepcopy(index)
        nodes = result["nodes"]
        for node_id, node in nodes.items():
            if not isinstance(node_id, str) or not isinstance(node, dict) or node.get("id") != node_id:
                raise ValueError("node ids must be strings matching their dictionary keys")
            if not isinstance(node.get("version"), str) or not node["version"]:
                raise ValueError(f"node {node_id} requires a source version")
            if node.get("review", "candidate") not in ("candidate", "reviewed"):
                raise ValueError(f"invalid review state for {node_id}")
            children = node.get("children", [])
            if not isinstance(children, list) or any(not isinstance(child, str) for child in children):
                raise ValueError(f"invalid children for {node_id}")
            if len(children) != len(set(children)):
                raise ValueError(f"duplicate children for {node_id}")
            node["children"] = children
            node["flags"] = _flags(node.get("flags", {}))
            node["stages"] = _stages(node.get("stages", {}))
            node["constraints"] = _strings(node.get("constraints", []), "constraints")
            node.setdefault("evidence", [])
            if not isinstance(node["evidence"], list):
                raise ValueError(f"invalid evidence for {node_id}")
            parent = node.get("parent")
            if parent is not None and parent not in nodes:
                raise ValueError(f"missing parent {parent} for {node_id}")
            for child in children:
                if child not in nodes or nodes[child].get("parent") != node_id:
                    raise ValueError(f"inconsistent child {child} for {node_id}")
            if parent is not None and node_id not in nodes[parent].get("children", []):
                raise ValueError(f"parent does not include child {node_id}")
            cls._ancestors(nodes, node_id)
        return result

    @staticmethod
    def _new_meta(node: dict) -> dict:
        return {
            "own_revision": 1, "summary_revision": 0, "delivery_revision": 0,
            "summary": node.get("summary", ""), "review": node.get("review", "candidate"),
            "pending": False, "changed": False, "pending_reasons": [],
            "delivery_flags": {key: [] for key in FLAG_KEYS}, "delivery_stages": {},
            "current_delivery": None, "last_summary_inputs": None, "reviewer": None,
            "delivery_evidence": {},
            "disposition": None,
            "summary_state": "reviewed_current" if node.get("review") == "reviewed" else "source_candidate",
        }

    @staticmethod
    def _source_signature(node: dict) -> str:
        # The scanner deliberately excludes generated regions from this semantic
        # version. Comparing body or raw evidence hashes here would reintroduce
        # generated-output feedback loops.
        return node["version"]

    @staticmethod
    def _manifest_signature(projects: list[dict]) -> str:
        signature = {}
        for project in projects:
            files = project.get("files", [])
            if isinstance(files, dict):
                files = [{"path": path, **(info if isinstance(info, dict) else {"sha256": info})}
                         for path, info in files.items()]
            signature[project.get("id", project.get("project_id", ""))] = {
                file["path"]: file.get("content_version", file.get("sha256"))
                for file in files if isinstance(file, dict) and "path" in file
            }
        return _digest(signature)

    @staticmethod
    def _source_evidence(node: dict) -> list[dict]:
        evidence = deepcopy(node.get("evidence", []))
        present = {item.get("path") for item in evidence if isinstance(item, dict)}
        for path in [node.get("entry"), *node.get("members", [])]:
            if path and path not in present:
                evidence.append({"path": path, "label": "source member"})
                present.add(path)
        return evidence

    @staticmethod
    def _scanned_files(state: dict, node: dict) -> dict:
        project = next((project for project in state["projects"]
                        if project.get("id", project.get("project_id")) == node.get("project_id")), {})
        files = project.get("files")
        if isinstance(files, dict):
            return {_relative_path(path): info if isinstance(info, dict) else {"sha256": info}
                    for path, info in files.items()}
        if isinstance(files, list):
            return {_relative_path(item["path"]): item for item in files if isinstance(item, dict) and item.get("path")}
        return {_relative_path(item["path"]): item for related in state["nodes"].values()
                if related.get("project_id") == node.get("project_id")
                for item in related.get("evidence", []) if isinstance(item, dict) and item.get("path")}

    @classmethod
    def _observe_delivery_evidence(cls, state: dict, node: dict, references: list[dict]) -> dict:
        manifest = cls._scanned_files(state, node)
        observed = {}
        for ref in references:
            path = _relative_path(ref["path"])
            current = manifest.get(path)
            observed[path] = {"path": path, "label": ref.get("label", path),
                              "task_ids": list(ref.get("task_ids", [])),
                              "sha256": current.get("sha256") if current else None,
                              "content_version": current.get("content_version", current.get("sha256")) if current else None,
                              "status": "scanned" if current else "missing_from_manifest"}
        return observed

    @staticmethod
    def _delivery_signature(references: dict) -> str:
        return _digest({path: {key: item.get(key) for key in ("status", "content_version")}
                        for path, item in references.items()})

    @staticmethod
    def _invalidate(state: dict, ids: list[str], reason: str) -> None:
        for node_id in ids:
            if node_id not in state["governance"]:
                continue
            meta = state["governance"][node_id]
            meta["pending"] = True
            meta["summary_state"] = "stale"
            if reason not in meta["pending_reasons"]:
                meta["pending_reasons"].append(reason)

    def refresh(self, index: dict) -> dict:
        """Merge a scan, preserving unrelated reviews, deliveries, and audit data."""
        index = self._validate_index(index)
        with self._locked():
            state = self._load()
            scanned_at_ns = index.get("scanned_at_ns", 0)
            if scanned_at_ns < state["scanned_at_ns"]:
                raise ConflictError("scan snapshot predates the current state; rescan before refreshing")
            if (scanned_at_ns and scanned_at_ns == state["scanned_at_ns"]
                    and index["scan_id"] != state["scan_id"]):
                raise ConflictError("two different scans reused the same capture timestamp")
            old_nodes = state["nodes"]
            new_nodes = index["nodes"]
            added = sorted(set(new_nodes) - set(old_nodes))
            removed = sorted(set(old_nodes) - set(new_nodes))
            source_changed = sorted(node_id for node_id in set(old_nodes) & set(new_nodes)
                                    if self._source_signature(old_nodes[node_id]) != self._source_signature(new_nodes[node_id]))
            evidence_state = {"nodes": new_nodes, "projects": index["projects"]}
            observations, delivery_evidence_changed = {}, []
            for node_id in sorted(set(old_nodes) & set(new_nodes)):
                previous = state["governance"][node_id]["delivery_evidence"]
                if not previous:
                    continue
                observed = self._observe_delivery_evidence(evidence_state, new_nodes[node_id], list(previous.values()))
                self._verify_evidence(evidence_state, new_nodes[node_id],
                                      [item for item in observed.values() if item["status"] == "scanned"])
                observations[node_id] = observed
                if self._delivery_signature(previous) != self._delivery_signature(observed):
                    delivery_evidence_changed.append(node_id)
            observation_changed = any(state["governance"][node_id]["delivery_evidence"] != observed
                                      for node_id, observed in observations.items())
            topology_changed = sorted(node_id for node_id in set(old_nodes) & set(new_nodes)
                                      if old_nodes[node_id]["children"] != new_nodes[node_id]["children"])
            raw_changed = sorted(node_id for node_id in set(old_nodes) & set(new_nodes)
                                 if old_nodes[node_id] != new_nodes[node_id])
            business_changed = bool(added or removed or source_changed or topology_changed or delivery_evidence_changed
                                    or self._manifest_signature(state["projects"]) != self._manifest_signature(index["projects"])
                                    or state["warnings"] != index.get("warnings", [])
                                    or state["scan_policy"] != index.get("scan_policy", {}))
            # A byte-for-byte repeated scan is a true no-op.
            if (not observation_changed and old_nodes == new_nodes and state["scan_id"] == index["scan_id"]
                    and state["projects"] == index["projects"]
                    and state["warnings"] == index.get("warnings", [])
                    and state["scan_policy"] == index.get("scan_policy", {})):
                if scanned_at_ns > state["scanned_at_ns"]:
                    # A newer identical observation advances the watermark but
                    # creates no change notification or summary invalidation.
                    state["scanned_at_ns"] = scanned_at_ns
                    self._save(state)
                return self._public(state)
            old_ancestors = {node_id: self._ancestors(old_nodes, node_id)
                             for node_id in removed + source_changed}
            for node_id in added + raw_changed:
                self._verify_evidence(evidence_state, new_nodes[node_id], self._source_evidence(new_nodes[node_id]))
            for node_id in removed:
                state["governance"].pop(node_id, None)
            for node_id in added:
                state["governance"][node_id] = self._new_meta(new_nodes[node_id])
            for node_id, observed in observations.items():
                state["governance"][node_id]["delivery_evidence"] = observed
            state["nodes"] = new_nodes
            for node_id in source_changed:
                node = new_nodes[node_id]
                meta = state["governance"][node_id]
                has_authored_summary = bool(meta["summary_revision"] or meta["delivery_revision"])
                meta["own_revision"] += 1
                meta["changed"] = True
                meta["review"] = "candidate"
                # A source edit does not prove an earlier blocker was resolved.
                # Preserve open declarations while clearing stale stage claims.
                meta["delivery_stages"] = {}
                meta["current_delivery"] = None
                if has_authored_summary:
                    self._invalidate(state, [node_id], "source changed after authored summary")
                else:
                    meta["summary"] = node.get("summary", "")
                    meta["summary_state"] = "source_candidate"
                self._invalidate(state, old_ancestors[node_id] + self._ancestors(new_nodes, node_id), f"source changed: {node_id}")
            for node_id in delivery_evidence_changed:
                meta = state["governance"][node_id]
                if node_id not in source_changed:
                    meta["own_revision"] += 1
                meta.update(changed=True, review="candidate", delivery_stages={})
                self._invalidate(state, [node_id] + self._ancestors(old_nodes, node_id)
                                 + self._ancestors(new_nodes, node_id), f"delivery evidence changed or missing: {node_id}")
            for node_id in removed:
                self._invalidate(state, old_ancestors[node_id], f"node removed: {node_id}")
            if old_nodes:
                for node_id in added:
                    self._invalidate(state, self._ancestors(new_nodes, node_id), f"node added: {node_id}")
            for node_id in topology_changed:
                state["governance"][node_id]["own_revision"] += 1
                self._invalidate(state, [node_id] + self._ancestors(new_nodes, node_id), f"children changed: {node_id}")
            state["scan_id"] = index["scan_id"]
            state["scanned_at_ns"] = scanned_at_ns
            state["projects"] = index["projects"]
            state["warnings"] = index.get("warnings", [])
            state["scan_policy"] = index.get("scan_policy", {})
            if business_changed:
                self._event(state, "scan_refreshed", added=added, removed=removed,
                            source_changed=source_changed, topology_changed=topology_changed,
                            delivery_evidence_changed=delivery_evidence_changed,
                            scan_id=state["scan_id"])
            self._save(state)
            return self._public(state)

    def _public(self, state: dict) -> dict:
        nodes: dict[str, dict] = {}

        def build(node_id: str) -> dict:
            if node_id in nodes:
                return nodes[node_id]
            source = state["nodes"][node_id]
            meta = state["governance"][node_id]
            children = [build(child_id) for child_id in source["children"]]
            node = deepcopy(source)
            node.update({key: deepcopy(meta[key]) for key in (
                "summary", "review", "pending", "changed", "pending_reasons",
                "own_revision", "summary_revision", "delivery_revision", "reviewer", "disposition",
                "summary_state",
            )})
            node["summary_inputs"] = deepcopy(meta["last_summary_inputs"])
            node["delivery_evidence"] = [deepcopy(item) for _, item in sorted(meta["delivery_evidence"].items())]
            node["source_version"] = source["version"]
            node["flags"] = {key: list(dict.fromkeys(source["flags"][key] + meta["delivery_flags"][key])) for key in FLAG_KEYS}
            node["stages"] = {**source["stages"], **meta["delivery_stages"]}
            node["aggregate_flags"] = {
                key: list(dict.fromkeys(node["flags"][key] + [item for child in children for item in child["aggregate_flags"][key]]))
                for key in FLAG_KEYS
            }
            node["flag_sources"] = {
                key: [{"node_id": node_id, "text": item} for item in node["flags"][key]]
                     + [item for child in children for item in child["flag_sources"][key]]
                for key in FLAG_KEYS
            }
            rollup = {
                "nodes": 1 + sum(child["rollup"]["nodes"] for child in children),
                "pending": int(node["pending"]) + sum(child["rollup"]["pending"] for child in children),
                "changed": int(node["changed"]) + sum(child["rollup"]["changed"] for child in children),
                "reviewed": int(node["review"] == "reviewed") + sum(child["rollup"]["reviewed"] for child in children),
                "candidate": int(node["review"] == "candidate") + sum(child["rollup"]["candidate"] for child in children),
                **{key: len(node["flag_sources"][key]) for key in FLAG_KEYS},
                "stages": {},
            }
            for stage in STAGE_KEYS:
                counts = {node["stages"][stage]: 1}
                for child in children:
                    for status, count in child["rollup"]["stages"][stage].items():
                        counts[status] = counts.get(status, 0) + count
                rollup["stages"][stage] = dict(sorted(counts.items()))
            node["rollup"] = rollup
            node["revision"] = _digest({
                "source": source["version"], "own_revision": meta["own_revision"],
                "summary_revision": meta["summary_revision"], "delivery_revision": meta["delivery_revision"],
                "pending": meta["pending"], "summary": meta["summary"], "review": meta["review"],
                "flags": node["flags"], "stages": node["stages"],
                "children": {child["id"]: child["revision"] for child in children},
            })
            nodes[node_id] = node
            return node

        for node_id in sorted(state["nodes"]):
            build(node_id)
        return {
            "schema": 1, "generation": state["generation"], "scan_id": state["scan_id"],
            "scanned_at_ns": state["scanned_at_ns"],
            "projects": deepcopy(state["projects"]), "nodes": nodes,
            "warnings": deepcopy(state["warnings"]), "scan_policy": deepcopy(state["scan_policy"]),
            "events": deepcopy(state["events"]), "viewed_generation": state["viewed_generation"],
            "unread_events": sum(event["sequence"] > state["viewed_generation"] for event in state["events"]),
        }

    def get_state(self) -> dict:
        with self._locked():
            return self._public(self._load())

    def pending(self) -> dict:
        with self._locked():
            state = self._load()
            public = self._public(state)
            queue = [node for node in public["nodes"].values() if node["pending"]]
            queue.sort(key=lambda node: (-len(self._ancestors(state["nodes"], node["id"])), node["id"]))
            for node in queue:
                node["eligible"] = not any(public["nodes"][child]["rollup"]["pending"] for child in node["children"])
            return {"generation": state["generation"], "scan_id": state["scan_id"], "count": len(queue), "nodes": queue}

    def _project_manifest(self, state: dict, node: dict) -> tuple[Path, dict[str, dict]]:
        project = next((project for project in state["projects"]
                        if project.get("id", project.get("project_id")) == node.get("project_id")), {})
        root_value = node.get("project_root") or project.get("root") or project.get("project_root")
        if not isinstance(root_value, str) or not root_value:
            raise ValueError(f"node {node['id']} has no source project root")
        root = Path(root_value).absolute()
        if not root.is_dir():
            raise ValueError(f"source project root does not exist for {node['id']}")
        return root, self._scanned_files(state, node)

    def _verify_evidence(self, state: dict, node: dict, evidence: Any) -> list[dict]:
        if not isinstance(evidence, list):
            raise ValueError("evidence must be a list")
        if not evidence:
            return []
        root, manifest = self._project_manifest(state, node)
        verified: list[dict] = []
        for item in evidence:
            if not isinstance(item, dict):
                raise ValueError("each evidence item must be an object")
            path = _relative_path(item.get("path"))
            if path not in manifest:
                raise ValueError(f"evidence was not included in the scanned source manifest: {path}")
            location = _safe_evidence_location(root, path)
            if not location.is_file():
                raise ConflictError(f"scanned evidence is missing: {path}; refresh the scan")
            expected = manifest[path].get("sha256")
            if not isinstance(expected, str) or not expected:
                raise ValueError(f"scanned evidence has no SHA-256: {path}")
            actual = _file_hash(location)
            if actual != expected:
                raise ConflictError(f"evidence changed since scanning: {path}; refresh the scan")
            if item.get("sha256") is not None and item["sha256"] != actual:
                raise ConflictError(f"delivery evidence SHA-256 mismatch: {path}")
            label = item.get("label", path)
            if not isinstance(label, str):
                raise ValueError("evidence.label must be a string")
            verified.append({**item, "path": path, "label": label, "sha256": actual})
        return verified

    def import_delivery(self, delivery: dict) -> dict:
        if not isinstance(delivery, dict):
            raise ValueError("delivery must be an object")
        delivery = deepcopy(delivery)
        for key in ("task_id", "node_id", "based_on_version"):
            if not isinstance(delivery.get(key), str) or not delivery[key].strip():
                raise ValueError(f"delivery requires {key}")
        fingerprint = _digest(delivery)
        with self._locked():
            state = self._load()
            task_id = delivery["task_id"]
            previous = state["deliveries"].get(task_id)
            if previous:
                if previous["fingerprint"] != fingerprint:
                    raise ConflictError(f"task_id already imported with different content: {task_id}")
                return {"status": "duplicate", "task_id": task_id, "node_id": previous["node_id"],
                        "generation": state["generation"],
                        "pending": state["governance"].get(previous["node_id"], {}).get("pending", False)}
            node = self._node(state, delivery["node_id"])
            if delivery["based_on_version"] != node["version"]:
                raise ConflictError(f"stale delivery source version for {node['id']}")
            changes = _strings(delivery.get("changes", []), "changes")
            unresolved = _strings(delivery.get("unresolved", []), "unresolved")
            validation = delivery.get("validation", {})
            if not isinstance(validation, dict):
                raise ValueError("delivery.validation must be an object")
            summary = delivery.get("summary_candidate", "")
            if not isinstance(summary, str):
                raise ValueError("summary_candidate must be a string")
            stages = _stages(delivery.get("stages", {}), partial=True)
            flags = _flags(delivery.get("flags", {}))
            flags["unverified"] = list(dict.fromkeys(flags["unverified"] + unresolved))
            evidence = self._verify_evidence(state, node, delivery.get("evidence", []))
            stored = {**delivery, "changes": changes, "unresolved": unresolved,
                      "evidence": evidence, "validation": validation, "stages": stages, "flags": flags}
            meta = state["governance"][node["id"]]
            observed = self._observe_delivery_evidence(state, node, evidence)
            for path, item in observed.items():
                previous_tasks = meta["delivery_evidence"].get(path, {}).get("task_ids", [])
                item["task_ids"] = list(dict.fromkeys(previous_tasks + [task_id]))
            meta["delivery_evidence"].update(observed)
            meta["own_revision"] += 1
            meta["delivery_revision"] += 1
            meta["summary"] = summary or meta["summary"]
            meta["review"] = "candidate"
            meta["changed"] = True
            meta["delivery_flags"] = {
                key: list(dict.fromkeys(meta["delivery_flags"][key] + flags[key])) for key in FLAG_KEYS
            }
            meta["delivery_stages"] = {**meta["delivery_stages"], **stages}
            meta["current_delivery"] = task_id
            self._invalidate(state, [node["id"]] + self._ancestors(state["nodes"], node["id"]), f"delivery imported: {task_id}")
            state["deliveries"][task_id] = {"fingerprint": fingerprint, "node_id": node["id"], "delivery": stored}
            self._event(state, "delivery_imported", task_id=task_id, node_id=node["id"],
                        source_version=node["version"], evidence=evidence, validation=validation)
            self._save(state)
            return {"status": "imported", "task_id": task_id, "node_id": node["id"],
                    "generation": state["generation"], "pending": True}

    def _preparation(self, state: dict, node_id: str) -> dict:
        self._node(state, node_id)
        public = self._public(state)
        node = public["nodes"][node_id]
        children = [public["nodes"][child_id] for child_id in node["children"]]
        blocked_children = [child["id"] for child in children if child["rollup"]["pending"]]
        if blocked_children:
            raise ConflictError("resolve child summaries first: " + ", ".join(blocked_children))
        child_summaries = [{"id": child["id"], "title": child.get("title", child["id"]),
                            "summary": child["summary"], "review": child["review"],
                            "version": child["version"], "revision": child["revision"],
                            "aggregate_flags": child["aggregate_flags"], "stages": child["stages"]}
                           for child in children]
        suggested = node["summary"]
        if children:
            parts = [node.get("purpose", "").strip()]
            parts += [f"{child['title']}: {child['summary']}" for child in child_summaries]
            suggested = "\n".join(part for part in parts if part)
        inputs = {child["id"]: child["revision"] for child in children}
        token = _digest({"node_id": node_id, "source_version": node["version"],
                         "own_revision": node["revision"], "input_versions": inputs})
        return {
            "schema": 1, "node_id": node_id, "source_version": node["version"],
            "own_revision": node["revision"], "input_versions": inputs, "token": token,
            "suggested_summary": suggested, "flags": node["aggregate_flags"],
            "child_summaries": child_summaries, "review": "candidate",
            "delivery_evidence": deepcopy(node["delivery_evidence"]),
            "notice": "Template text is a candidate. Reviewed acceptance requires an explicit reviewer.",
        }

    def prepare_summary(self, node_id: str) -> dict:
        with self._locked():
            return self._preparation(self._load(), node_id)

    def _verify_subtree_evidence(self, state: dict, node_id: str) -> list[dict]:
        seen: set[tuple[str, str]] = set()
        references: list[dict] = []

        def visit(current_id: str) -> None:
            node = state["nodes"][current_id]
            evidence = []
            for item in self._source_evidence(node):
                if not isinstance(item, dict):
                    raise ValueError("each evidence item must be an object")
                key = (node.get("project_id", ""), _relative_path(item.get("path")))
                if key not in seen:
                    evidence.append(item)
                    seen.add(key)
            for item in state["governance"][current_id]["delivery_evidence"].values():
                key = (node.get("project_id", ""), item["path"])
                if key not in seen:
                    evidence.append(item)
                    seen.add(key)
            references.extend({"node_id": current_id, "project_id": node.get("project_id"), **item}
                              for item in self._verify_evidence(state, node, evidence))
            for child_id in node["children"]:
                visit(child_id)

        visit(node_id)
        return references

    def commit_summary(self, proposal: dict) -> dict:
        if not isinstance(proposal, dict):
            raise ValueError("summary proposal must be an object")
        node_id = proposal.get("node_id")
        token = proposal.get("token")
        summary = proposal.get("summary", proposal.get("suggested_summary"))
        review = proposal.get("review", "candidate")
        reviewer = proposal.get("reviewer")
        disposition = proposal.get("disposition")
        if not isinstance(node_id, str) or not isinstance(token, str) or not token:
            raise ValueError("proposal requires node_id and preparation token")
        if not isinstance(summary, str) or not summary.strip():
            raise ValueError("summary must be a non-empty string")
        if review not in ("candidate", "reviewed"):
            raise ValueError("review must be candidate or reviewed")
        if review == "reviewed" and (not isinstance(reviewer, str) or not reviewer.strip()):
            raise ValueError("reviewed summaries require an explicit reviewer")
        if reviewer is not None and not isinstance(reviewer, str):
            raise ValueError("reviewer must be a string")
        if disposition is not None and disposition not in ("updated", "no-material-change"):
            raise ValueError("disposition must be updated or no-material-change")
        fingerprint = _digest({"node_id": node_id, "token": token, "summary": summary,
                               "review": review, "reviewer": reviewer, "disposition": disposition,
                               "source_version": proposal.get("source_version"),
                               "own_revision": proposal.get("own_revision"),
                               "input_versions": proposal.get("input_versions")})
        with self._locked():
            state = self._load()
            previous = state["commits"].get(token)
            if previous:
                if previous["fingerprint"] != fingerprint:
                    raise ConflictError("preparation token was already committed with different content")
                return {**deepcopy(previous["result"]), "status": "duplicate"}
            self._node(state, node_id)
            current = self._preparation(state, node_id)
            for field in ("token", "own_revision", "input_versions", "source_version"):
                if proposal.get(field) != current[field]:
                    raise ConflictError(f"summary inputs changed ({field}); prepare a new summary")
            verified_evidence = self._verify_subtree_evidence(state, node_id)
            meta = state["governance"][node_id]
            meta["summary"] = summary
            meta["summary_revision"] += 1
            meta["own_revision"] += 1
            meta["review"] = review
            meta["reviewer"] = reviewer
            meta["disposition"] = disposition or "updated"
            meta["pending"] = review != "reviewed"
            meta["summary_state"] = "reviewed_current" if review == "reviewed" else "candidate_current"
            if review == "reviewed":
                meta["pending_reasons"] = []
            elif "candidate summary awaiting review" not in meta["pending_reasons"]:
                meta["pending_reasons"].append("candidate summary awaiting review")
            meta["last_summary_inputs"] = {key: deepcopy(current[key]) for key in ("source_version", "own_revision", "input_versions")}
            meta["last_summary_inputs"]["evidence"] = verified_evidence
            self._invalidate(state, self._ancestors(state["nodes"], node_id), f"summary changed: {node_id}")
            self._event(state, "summary_committed", node_id=node_id, review=review, reviewer=reviewer,
                        disposition=meta["disposition"], token=token, source_version=current["source_version"],
                        input_versions=current["input_versions"], evidence=verified_evidence, summary=summary)
            public = self._public(state)
            result = {"status": "committed", "node_id": node_id, "generation": state["generation"],
                      "pending": meta["pending"], "review": review, "summary": summary,
                      "revision": public["nodes"][node_id]["revision"]}
            state["commits"][token] = {"fingerprint": fingerprint, "result": result}
            self._save(state)
            return result

    def export_context(self, node_id: str) -> dict:
        with self._locked():
            state = self._load()
            self._node(state, node_id)
            public = self._public(state)
            node = public["nodes"][node_id]
            ancestor_ids = list(reversed(self._ancestors(state["nodes"], node_id)))
            ancestors = [public["nodes"][ancestor_id] for ancestor_id in ancestor_ids]
            dependencies = []
            for relation in node.get("related", []):
                related_id = relation.get("id") if isinstance(relation, dict) else None
                if related_id in public["nodes"]:
                    dependencies.append({"relation": relation.get("relation", "related"), "node": public["nodes"][related_id]})
                else:
                    dependencies.append({"relation": deepcopy(relation), "missing": True})
            children = [public["nodes"][child_id] for child_id in node["children"]]
            context_nodes = ancestors + [node] + children + [item["node"] for item in dependencies if "node" in item]
            constraints = []
            for owner in ancestors + [node]:
                constraints.extend({"node_id": owner["id"], "text": text} for text in owner.get("constraints", []))
            return {
                "schema": 1, "scan_id": state["scan_id"], "generation": state["generation"],
                "node": node, "ancestors": ancestors, "children": children,
                "dependencies": dependencies, "constraints": constraints,
                "source_versions": {item["id"]: item["version"] for item in context_nodes},
                "revision_tokens": {item["id"]: item["revision"] for item in context_nodes},
                "evidence": [{"node_id": item["id"], **deepcopy(evidence)}
                             for item in context_nodes for evidence in item.get("evidence", []) + item["delivery_evidence"]],
                "scan_policy": deepcopy(state["scan_policy"]),
                "protocol": {
                    "delivery_requires": ["task_id", "node_id", "based_on_version", "changes", "evidence", "validation", "unresolved", "summary_candidate", "stages"],
                    "summary_order": "Resolve pending descendants before reviewing their parent.",
                    "review_boundary": "Generated summaries and imported deliveries remain candidates until explicitly reviewed.",
                    "refresh_boundary": "Refresh discovery before imports and commits; evidence is rehashed, while newly added files require a fresh scan.",
                    "stage_boundary": "Delivery, integration, execution, and acceptance are independent declarations; validation text does not promote a stage.",
                },
            }

    def mark_viewed(self) -> dict:
        """Acknowledge notifications without accepting summaries or clearing flags."""
        with self._locked():
            state = self._load()
            had_changes = any(meta["changed"] for meta in state["governance"].values())
            if state["viewed_generation"] != state["generation"] or had_changes:
                for meta in state["governance"].values():
                    meta["changed"] = False
                self._event(state, "viewed")
                state["viewed_generation"] = state["generation"]
                self._save(state)
            return self._public(state)
