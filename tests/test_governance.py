"""Behavioral tests for durable, versioned document governance."""

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from doctree.governance import ConflictError, StateStore


class GovernanceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.path = self.root / "state.json"
        self.store = StateStore(self.path)
        self.index = self.make_index()
        self.store.refresh(self.index)

    def make_index(self):
        shape = {
            "root": (None, ["alpha", "beta"]),
            "alpha": ("root", ["a"]),
            "beta": ("root", ["b"]),
            "a": ("alpha", []),
            "b": ("beta", []),
        }
        nodes = {}
        files = []
        for node_id, (parent, children) in shape.items():
            path = f"{node_id}.md"
            content = f"# {node_id}\nInitial source for {node_id}.\n"
            (self.source / path).write_text(content, encoding="utf-8")
            sha = hashlib.sha256((self.source / path).read_bytes()).hexdigest()
            evidence = {"path": path, "label": node_id + " source", "sha256": sha}
            files.append(evidence)
            nodes[node_id] = {
                "id": node_id, "title": node_id.upper(), "purpose": "Purpose " + node_id,
                "kind": "directory" if children else "document", "parent": parent,
                "children": children, "related": [], "project_id": "project",
                "project_root": str(self.source), "entry": path, "source_type": "local_copy",
                "review": "candidate", "summary": "Initial summary " + node_id,
                "body": content, "evidence": [evidence], "constraints": [],
                "flags": {"blocked": [], "unverified": [], "decisions": []},
                "stages": {"delivery": "unknown", "integration": "not_integrated",
                           "execution": "not_executed", "acceptance": "not_accepted"},
                "version": sha,
            }
        nodes["root"]["constraints"] = ["No production experiment past Gate14."]
        nodes["a"]["flags"]["unverified"] = ["CPU result is not NPU evidence."]
        nodes["b"]["flags"]["blocked"] = ["Missing acceptance record."]
        nodes["a"]["related"] = [{"id": "b", "relation": "depends_on"}]
        return {
            "schema": 1, "scan_id": "scan-1", "nodes": nodes,
            "projects": [{"id": "project", "root": str(self.source), "files": files}],
            "warnings": [], "scan_policy": {"excluded": [".doctree", ".git"]},
        }

    def delivery(self, node_id="a", task_id=None):
        return {
            "task_id": task_id or "task-" + node_id, "node_id": node_id,
            "based_on_version": self.index["nodes"][node_id]["version"],
            "changes": ["Updated the document explanation."],
            "evidence": [{"path": node_id + ".md", "label": "scanned source"}],
            "validation": {"local_check": "passed", "production": "not_run"},
            "unresolved": ["Production acceptance is still pending."],
            "summary_candidate": "Delivery candidate " + node_id,
            "stages": {"delivery": "delivered"},
        }

    def review(self, node_id, summary=None):
        proposal = self.store.prepare_summary(node_id)
        proposal.update(summary=summary or f"Reviewed current evidence for {node_id}.",
                        review="reviewed", reviewer="test-reviewer")
        return self.store.commit_summary(proposal)

    def change_source(self, node_id):
        index = deepcopy(self.index)
        content = f"# {node_id}\nChanged source, including a new limitation.\n"
        (self.source / f"{node_id}.md").write_text(content, encoding="utf-8")
        sha = hashlib.sha256((self.source / f"{node_id}.md").read_bytes()).hexdigest()
        node = index["nodes"][node_id]
        node.update(version=sha, body=content, summary="New scanner candidate " + node_id)
        node["evidence"][0]["sha256"] = sha
        for evidence in index["projects"][0]["files"]:
            if evidence["path"] == f"{node_id}.md":
                evidence["sha256"] = sha
        index["scan_id"] = "scan-2"
        self.index = index
        return index

    def test_scan_is_idempotent_and_candidate_is_not_automatically_pending(self):
        before = self.store.get_state()
        after = self.store.refresh(deepcopy(self.index))
        self.assertEqual(before, after)
        self.assertEqual(self.store.pending()["count"], 0)
        self.assertEqual(after["nodes"]["root"]["review"], "candidate")
        self.assertEqual(after["nodes"]["root"]["rollup"]["candidate"], 5)

    def test_leaf_source_change_invalidates_only_ancestors_and_preserves_sibling(self):
        self.review("b")
        self.review("beta")
        before = self.store.get_state()
        after = self.store.refresh(self.change_source("a"))
        self.assertEqual({node["id"] for node in self.store.pending()["nodes"]}, {"alpha", "root"})
        self.assertTrue(after["nodes"]["a"]["changed"])
        self.assertFalse(after["nodes"]["a"]["pending"])
        self.assertEqual(after["nodes"]["a"]["summary"], "New scanner candidate a")
        self.assertEqual(after["nodes"]["root"]["summary"], before["nodes"]["root"]["summary"])
        for node_id in ("b", "beta"):
            self.assertEqual(after["nodes"][node_id]["revision"], before["nodes"][node_id]["revision"])
            self.assertEqual(after["nodes"][node_id]["review"], "reviewed")

    def test_authored_summary_becomes_pending_when_own_source_changes(self):
        self.review("a", "Evidence reviewed before source revision.")
        after = self.store.refresh(self.change_source("a"))
        node = after["nodes"]["a"]
        self.assertTrue(node["pending"])
        self.assertEqual(node["review"], "candidate")
        self.assertEqual(node["summary"], "Evidence reviewed before source revision.")

    def test_two_deliveries_survive_reopen_and_resolve_bottom_up(self):
        self.store.import_delivery(self.delivery("a"))
        StateStore(self.path).import_delivery(self.delivery("b"))
        self.store = StateStore(self.path)
        self.assertEqual(self.store.pending()["count"], 5)
        with self.assertRaises(ConflictError):
            self.store.prepare_summary("root")
        with self.assertRaises(ConflictError):
            self.store.prepare_summary("alpha")
        self.review("a")
        self.assertTrue(self.store.get_state()["nodes"]["b"]["pending"])
        self.review("alpha")
        self.review("b")
        self.review("beta")
        self.review("root")
        self.assertEqual(StateStore(self.path).pending()["count"], 0)
        self.assertEqual(self.store.get_state()["nodes"]["root"]["rollup"]["reviewed"], 5)
        kinds = [event["kind"] for event in self.store.get_state()["events"]]
        self.assertEqual(kinds.count("delivery_imported"), 2)
        self.assertEqual(kinds.count("summary_committed"), 5)

    def test_candidate_commit_stays_pending_and_requires_new_review_preparation(self):
        self.store.import_delivery(self.delivery())
        proposal = self.store.prepare_summary("a")
        result = self.store.commit_summary(proposal)
        self.assertTrue(result["pending"])
        self.assertEqual(result["review"], "candidate")
        candidate = self.store.get_state()["nodes"]["a"]
        self.assertEqual(candidate["summary_state"], "candidate_current")
        self.assertEqual(candidate["summary_inputs"]["source_version"], proposal["source_version"])
        self.assertEqual(self.store.commit_summary(proposal)["status"], "duplicate")
        changed = {**proposal, "review": "reviewed", "reviewer": "reviewer"}
        with self.assertRaises(ConflictError):
            self.store.commit_summary(changed)
        self.assertFalse(self.review("a")["pending"])
        self.assertEqual(self.store.get_state()["nodes"]["a"]["summary_state"], "reviewed_current")

    def test_review_requires_named_reviewer_and_supports_no_material_change(self):
        proposal = self.store.prepare_summary("a")
        proposal["review"] = "reviewed"
        with self.assertRaises(ValueError):
            self.store.commit_summary(proposal)
        proposal.update(reviewer="Codex evidence review", disposition="no-material-change")
        self.store.commit_summary(proposal)
        self.assertEqual(self.store.get_state()["nodes"]["a"]["disposition"], "no-material-change")

    def test_parent_preparation_rejects_changed_inputs_even_after_children_resolved(self):
        proposal = self.store.prepare_summary("root")
        self.store.import_delivery(self.delivery())
        self.review("a")
        self.review("alpha")
        proposal.update(review="reviewed", reviewer="reviewer")
        with self.assertRaises(ConflictError):
            self.store.commit_summary(proposal)

    def test_unrelated_branch_change_does_not_reject_eligible_subtree_proposal(self):
        proposal = self.store.prepare_summary("alpha")
        self.store.import_delivery(self.delivery("b"))
        proposal.update(review="reviewed", reviewer="reviewer")
        self.assertFalse(self.store.commit_summary(proposal)["pending"])
        self.assertTrue(self.store.get_state()["nodes"]["b"]["pending"])

    def test_own_delivery_revision_invalidates_prepared_leaf(self):
        proposal = self.store.prepare_summary("a")
        self.store.import_delivery(self.delivery())
        proposal.update(review="reviewed", reviewer="reviewer")
        with self.assertRaises(ConflictError):
            self.store.commit_summary(proposal)

    def test_delivery_is_exactly_idempotent_and_conflicting_duplicate_is_rejected(self):
        delivery = self.delivery()
        first = self.store.import_delivery(delivery)
        second = StateStore(self.path).import_delivery(deepcopy(delivery))
        self.assertEqual(second["status"], "duplicate")
        self.assertEqual(first["generation"], second["generation"])
        delivery["summary_candidate"] = "Different content with reused task id."
        with self.assertRaises(ConflictError):
            self.store.import_delivery(delivery)
        self.assertEqual(self.store.get_state()["generation"], first["generation"])

    def test_stale_delivery_and_tampered_preparation_are_rejected_without_mutation(self):
        delivery = self.delivery()
        delivery["based_on_version"] = "old-source-version"
        before = self.path.read_bytes()
        with self.assertRaises(ConflictError):
            self.store.import_delivery(delivery)
        proposal = self.store.prepare_summary("a")
        proposal["input_versions"] = {"unrelated": "fake"}
        with self.assertRaises(ConflictError):
            self.store.commit_summary(proposal)
        self.assertEqual(before, self.path.read_bytes())

    def test_evidence_must_be_scanned_local_and_hash_current(self):
        for path in ("../outside.md", "C:/outside.md", "/outside.md", "not-scanned.md"):
            delivery = self.delivery(task_id="invalid-" + path)
            delivery["evidence"] = [{"path": path, "label": "bad"}]
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.store.import_delivery(delivery)
        delivery = self.delivery()
        delivery["evidence"][0]["sha256"] = "incorrect"
        with self.assertRaises(ConflictError):
            self.store.import_delivery(delivery)
        (self.source / "a.md").write_text("Changed without scan", encoding="utf-8")
        with self.assertRaises(ConflictError):
            self.store.import_delivery(self.delivery())

    def test_unscanned_evidence_edit_rejects_summary_commit_atomically(self):
        proposal = self.store.prepare_summary("alpha")
        proposal.update(review="reviewed", reviewer="reviewer")
        before = self.path.read_bytes()
        (self.source / "a.md").write_text("Changed after preparation", encoding="utf-8")
        with self.assertRaises(ConflictError):
            self.store.commit_summary(proposal)
        self.assertEqual(before, self.path.read_bytes())

    def test_flags_roll_up_deterministically_without_promoting_independent_stages(self):
        delivery = self.delivery()
        delivery["flags"] = {"blocked": ["Missing acceptance record."], "decisions": ["Keep Gate14 frozen."]}
        self.store.import_delivery(delivery)
        state = self.store.get_state()
        root = state["nodes"]["root"]
        self.assertEqual(root["aggregate_flags"]["blocked"], ["Missing acceptance record."])
        self.assertEqual(root["rollup"]["blocked"], 2)
        self.assertEqual(root["aggregate_flags"]["decisions"], ["Keep Gate14 frozen."])
        self.assertEqual(root["aggregate_flags"]["unverified"],
                         ["CPU result is not NPU evidence.", "Production acceptance is still pending."])
        node = state["nodes"]["a"]
        self.assertEqual(node["stages"], {"delivery": "delivered", "integration": "not_integrated",
                                          "execution": "not_executed", "acceptance": "not_accepted"})
        self.assertEqual(node["review"], "candidate")
        self.review("a")
        self.assertEqual(self.store.get_state()["nodes"]["a"]["stages"]["acceptance"], "not_accepted")

    def test_mark_viewed_clears_notifications_without_accepting_pending_work(self):
        self.store.import_delivery(self.delivery())
        before = self.store.get_state()
        after = self.store.mark_viewed()
        self.assertEqual(after["unread_events"], 0)
        self.assertFalse(after["nodes"]["a"]["changed"])
        self.assertTrue(after["nodes"]["a"]["pending"])
        self.assertEqual(after["nodes"]["a"]["summary_state"], "stale")
        self.assertEqual(after["nodes"]["a"]["review"], "candidate")
        self.assertEqual(after["nodes"]["a"]["revision"], before["nodes"]["a"]["revision"])
        self.assertEqual(after, self.store.mark_viewed())

    def test_context_preserves_ancestor_constraints_dependencies_and_versions(self):
        context = self.store.export_context("a")
        self.assertEqual([node["id"] for node in context["ancestors"]], ["root", "alpha"])
        self.assertEqual(context["constraints"], [{"node_id": "root", "text": "No production experiment past Gate14."}])
        self.assertEqual(context["dependencies"][0]["node"]["id"], "b")
        self.assertEqual(context["dependencies"][0]["relation"], "depends_on")
        self.assertEqual(set(context["source_versions"]), {"root", "alpha", "a", "b"})
        self.assertEqual(context["scan_policy"], {"excluded": [".doctree", ".git"]})

    def test_removed_child_invalidates_surviving_ancestors_and_preserves_audit(self):
        self.store.import_delivery(self.delivery("a"))
        index = deepcopy(self.index)
        del index["nodes"]["a"]
        index["nodes"]["alpha"]["children"] = []
        index["scan_id"] = "scan-removed"
        self.store.refresh(index)
        state = self.store.get_state()
        self.assertNotIn("a", state["nodes"])
        self.assertTrue(state["nodes"]["alpha"]["pending"])
        self.assertFalse(state["nodes"]["beta"]["pending"])
        self.assertTrue(any(event.get("task_id") == "task-a" for event in state["events"]))
        self.assertEqual(self.store.import_delivery(self.delivery("a"))["status"], "duplicate")

    def test_invalid_tree_and_corrupt_snapshot_are_not_overwritten(self):
        index = deepcopy(self.index)
        index["nodes"]["root"]["parent"] = "a"
        before = self.path.read_bytes()
        with self.assertRaises(ValueError):
            self.store.refresh(index)
        self.assertEqual(before, self.path.read_bytes())
        self.path.write_text("{broken", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.store.get_state()
        self.assertEqual(self.path.read_text(encoding="utf-8"), "{broken")

    def test_two_process_leaf_imports_do_not_lose_updates(self):
        barrier = self.root / "start"
        script = (
            "import json, pathlib, sys, time\n"
            "from doctree.governance import StateStore\n"
            "while not pathlib.Path(sys.argv[3]).exists(): time.sleep(0.01)\n"
            "delivery=json.loads(pathlib.Path(sys.argv[2]).read_text(encoding='utf-8'))\n"
            "result=StateStore(sys.argv[1]).import_delivery(delivery)\n"
            "print(result['status'])\n"
        )
        processes = []
        repository = str(Path(__file__).resolve().parents[1])
        environment = dict(os.environ)
        environment["PYTHONPATH"] = repository + os.pathsep + environment.get("PYTHONPATH", "")
        for node_id in ("a", "b"):
            delivery_path = self.root / f"delivery-{node_id}.json"
            delivery_path.write_text(json.dumps(self.delivery(node_id)), encoding="utf-8")
            processes.append(subprocess.Popen(
                [sys.executable, "-X", "utf8", "-c", script, str(self.path), str(delivery_path), str(barrier)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=environment,
            ))
        try:
            barrier.touch()
            for process in processes:
                output, errors = process.communicate(timeout=20)
                self.assertEqual(process.returncode, 0, errors)
                self.assertEqual(output.strip(), "imported")
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                    process.communicate()
        state = self.store.get_state()
        self.assertEqual(state["generation"], 3)
        self.assertTrue(state["nodes"]["a"]["pending"])
        self.assertTrue(state["nodes"]["b"]["pending"])
        self.assertEqual({event.get("task_id") for event in state["events"] if event["kind"] == "delivery_imported"}, {"task-a", "task-b"})

    def test_old_scan_cannot_overwrite_newer_observation_even_when_latest_was_identical(self):
        newer = deepcopy(self.index)
        newer["scanned_at_ns"] = 20
        generation = self.store.get_state()["generation"]
        observed = self.store.refresh(newer)
        self.assertEqual(observed["scanned_at_ns"], 20)
        self.assertEqual(observed["generation"], generation)
        older = deepcopy(self.index)
        older["scanned_at_ns"] = 10
        with self.assertRaises(ConflictError):
            self.store.refresh(older)
        same_timestamp = deepcopy(newer)
        same_timestamp["scan_id"] = "different-scan"
        with self.assertRaises(ConflictError):
            self.store.refresh(same_timestamp)
        self.assertEqual(self.store.get_state(), observed)

    def test_stale_scan_bytes_rejected_before_overwriting_source_state(self):
        changed = self.change_source("a")
        (self.source / "a.md").write_text("A third edit after the scan completed.", encoding="utf-8")
        before = self.path.read_bytes()
        with self.assertRaises(ConflictError):
            self.store.refresh(changed)
        self.assertEqual(before, self.path.read_bytes())

    def test_later_delivery_keeps_prior_unresolved_flags_and_unspecified_stages(self):
        first = self.delivery(task_id="first")
        first["stages"]["integration"] = "integrated_locally"
        self.store.import_delivery(first)
        later = self.delivery(task_id="later")
        later["unresolved"] = ["A second open question."]
        self.store.import_delivery(later)
        node = self.store.get_state()["nodes"]["a"]
        self.assertEqual(node["stages"]["integration"], "integrated_locally")
        self.assertIn("Production acceptance is still pending.", node["flags"]["unverified"])
        self.assertIn("A second open question.", node["flags"]["unverified"])

    def test_generated_only_raw_edit_updates_manifest_without_invalidating_summaries(self):
        index = deepcopy(self.index)
        for item in index["projects"][0]["files"]:
            item["content_version"] = item["sha256"]
        self.store.refresh(index)
        proposal = self.store.prepare_summary("alpha")
        before = self.store.get_state()
        content = (self.source / "a.md").read_text(encoding="utf-8") + "\n<!-- generated -->New projection<!-- /generated -->\n"
        (self.source / "a.md").write_text(content, encoding="utf-8")
        raw_hash = hashlib.sha256((self.source / "a.md").read_bytes()).hexdigest()
        index["nodes"]["a"]["body"] = content
        index["nodes"]["a"]["evidence"][0]["sha256"] = raw_hash
        for item in index["projects"][0]["files"]:
            if item["path"] == "a.md":
                item["sha256"] = raw_hash
        after = self.store.refresh(index)
        self.assertEqual(after["generation"], before["generation"])
        self.assertEqual(after["events"], before["events"])
        self.assertEqual(self.store.pending()["count"], 0)
        for node_id in before["nodes"]:
            self.assertEqual(after["nodes"][node_id]["revision"], before["nodes"][node_id]["revision"])
        proposal.update(review="reviewed", reviewer="reviewer")
        self.assertEqual(self.store.commit_summary(proposal)["status"], "committed")

    def test_source_members_without_explicit_evidence_are_rehashed_before_commit(self):
        index = deepcopy(self.index)
        index["nodes"]["a"]["evidence"] = []
        index["nodes"]["a"]["members"] = ["a.md"]
        self.store.refresh(index)
        proposal = self.store.prepare_summary("alpha")
        proposal.update(review="reviewed", reviewer="reviewer")
        (self.source / "a.md").write_text("Edited an implicit source member", encoding="utf-8")
        with self.assertRaises(ConflictError):
            self.store.commit_summary(proposal)

    def test_source_edit_does_not_silently_resolve_delivery_flags(self):
        self.store.import_delivery(self.delivery())
        self.review("a")
        state = self.store.refresh(self.change_source("a"))
        self.assertIn("Production acceptance is still pending.", state["nodes"]["root"]["aggregate_flags"]["unverified"])
        self.assertEqual(state["nodes"]["a"]["stages"]["delivery"], "unknown")
        self.assertEqual(state["nodes"]["a"]["summary_state"], "stale")

    def test_delivery_only_evidence_change_invalidates_reviewed_leaf_and_ancestors(self):
        extra = self.source / "extra.md"
        extra.write_text("Additional delivery evidence v1", encoding="utf-8")
        first_sha = hashlib.sha256(extra.read_bytes()).hexdigest()
        self.index["projects"][0]["files"].append({"path": "extra.md", "sha256": first_sha})
        self.store.refresh(self.index)
        delivery = self.delivery()
        delivery["evidence"] = [{"path": "extra.md", "label": "Delivery-only evidence"}]
        self.store.import_delivery(delivery)
        self.review("a", "Reviewed delivery-only evidence before it changed.")
        self.review("alpha")
        self.review("root")
        self.assertEqual(self.store.pending()["count"], 0)
        before = self.store.get_state()
        stale = self.store.prepare_summary("a")
        stale.update(review="reviewed", reviewer="reviewer")
        extra.write_text("Additional delivery evidence v2", encoding="utf-8")
        new_sha = hashlib.sha256(extra.read_bytes()).hexdigest()
        changed = deepcopy(self.index)
        changed["projects"][0]["files"][-1]["sha256"] = new_sha
        after = self.store.refresh(changed)
        self.assertEqual({node["id"] for node in self.store.pending()["nodes"]}, {"a", "alpha", "root"})
        self.assertEqual(after["nodes"]["a"]["summary"], before["nodes"]["a"]["summary"])
        self.assertEqual(after["nodes"]["a"]["summary_state"], "stale")
        self.assertEqual(after["nodes"]["a"]["review"], "candidate")
        self.assertEqual(after["nodes"]["b"]["revision"], before["nodes"]["b"]["revision"])
        self.assertEqual(after["nodes"]["a"]["stages"]["acceptance"], "not_accepted")
        saved = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(saved["deliveries"]["task-a"]["delivery"]["evidence"][0]["sha256"], first_sha)
        self.assertEqual(after["nodes"]["a"]["delivery_evidence"][0]["sha256"], new_sha)
        with self.assertRaises(ConflictError):
            self.store.commit_summary(stale)
        self.review("a", "Explicitly reviewed the revised additional evidence.")
        self.review("alpha")
        self.review("root")
        self.assertEqual(self.store.pending()["count"], 0)
        accepted = self.store.get_state()["nodes"]["a"]["summary_inputs"]["evidence"]
        self.assertEqual(next(item["sha256"] for item in accepted if item["path"] == "extra.md"), new_sha)
        refreshed = self.store.refresh(changed)
        self.assertEqual(refreshed["nodes"]["a"]["summary_state"], "reviewed_current")

    def _import_additional_evidence(self, content_version=None):
        extra = self.source / "extra.md"
        extra.write_text("Additional evidence", encoding="utf-8")
        sha = hashlib.sha256(extra.read_bytes()).hexdigest()
        info = {"path": "extra.md", "sha256": sha}
        if content_version:
            info["content_version"] = content_version
        self.index["projects"][0]["files"].append(info)
        self.store.refresh(self.index)
        delivery = self.delivery()
        delivery["evidence"] = [{"path": "extra.md"}]
        self.store.import_delivery(delivery)
        self.review("a")
        self.review("alpha")
        self.review("root")
        return extra, sha

    def test_dynamic_evidence_missing_restore_and_unscanned_change_remain_explicit(self):
        extra, sha = self._import_additional_evidence()
        extra.unlink()
        missing = deepcopy(self.index)
        missing["projects"][0]["files"] = [item for item in missing["projects"][0]["files"] if item["path"] != "extra.md"]
        state = self.store.refresh(missing)
        self.assertEqual(state["nodes"]["a"]["delivery_evidence"][0]["status"], "missing_from_manifest")
        self.assertEqual({node["id"] for node in self.store.pending()["nodes"]}, {"a", "alpha", "root"})
        with self.assertRaisesRegex(ValueError, "manifest"):
            self.review("a")
        extra.write_text("Additional evidence", encoding="utf-8")
        self.store.refresh(self.index)
        self.assertTrue(self.store.get_state()["nodes"]["a"]["pending"])
        proposal = self.store.prepare_summary("a")
        proposal.update(review="reviewed", reviewer="reviewer")
        extra.write_text("An edit after preparation", encoding="utf-8")
        before = self.path.read_bytes()
        with self.assertRaises(ConflictError):
            self.store.commit_summary(proposal)
        with self.assertRaises(ConflictError):
            self.store.refresh(self.index)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(json.loads(before)["deliveries"]["task-a"]["delivery"]["evidence"][0]["sha256"], sha)

    def test_dynamic_generated_only_observation_preserves_review_and_accepts_latest_raw_hash(self):
        extra, _ = self._import_additional_evidence(content_version="same-semantic-content")
        before = self.store.get_state()
        proposal = self.store.prepare_summary("a")
        proposal.update(review="reviewed", reviewer="reviewer")
        extra.write_text("Additional evidence\n<!-- generated-only navigation -->", encoding="utf-8")
        changed = deepcopy(self.index)
        changed["projects"][0]["files"][-1]["sha256"] = hashlib.sha256(extra.read_bytes()).hexdigest()
        after = self.store.refresh(changed)
        self.assertEqual(after["generation"], before["generation"])
        self.assertEqual(after["nodes"]["a"]["revision"], before["nodes"]["a"]["revision"])
        self.assertEqual(self.store.pending()["count"], 0)
        self.assertEqual(self.store.commit_summary(proposal)["status"], "committed")

    def test_old_snapshot_delivery_evidence_is_migrated_from_recorded_hash(self):
        extra, original_sha = self._import_additional_evidence()
        saved = json.loads(self.path.read_text(encoding="utf-8"))
        for meta in saved["governance"].values():
            meta.pop("delivery_evidence")
        self.path.write_text(json.dumps(saved), encoding="utf-8")
        extra.write_text("Changed while still using the older application", encoding="utf-8")
        changed = deepcopy(self.index)
        changed["projects"][0]["files"][-1]["sha256"] = hashlib.sha256(extra.read_bytes()).hexdigest()
        migrated = StateStore(self.path).refresh(changed)
        self.assertTrue(migrated["nodes"]["a"]["pending"])
        self.assertEqual(migrated["nodes"]["a"]["summary_state"], "stale")
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))["deliveries"]["task-a"]["delivery"]["evidence"][0]["sha256"], original_sha)

    def test_evidence_symlink_is_rejected_before_reading_its_external_target(self):
        outside = self.root / "outside.md"
        outside.write_text("Outside content", encoding="utf-8")
        link = self.source / "linked.md"
        try:
            link.symlink_to(outside)
        except OSError as error:
            self.skipTest(f"Symlinks unavailable: {error}")
        self.index["projects"][0]["files"].append({"path": "linked.md", "sha256": "declared-hash"})
        self.store.refresh(self.index)
        delivery = self.delivery()
        delivery["evidence"] = [{"path": "linked.md"}]
        with patch("doctree.governance._file_hash", side_effect=AssertionError("must not read external bytes")) as hashing:
            with self.assertRaisesRegex(ValueError, "symbolic link|junction"):
                self.store.import_delivery(delivery)
            hashing.assert_not_called()

    def test_explicitly_partial_scan_preserves_review_delivery_and_recovery(self):
        self.store.import_delivery(self.delivery())
        self.review("a", "Do not discard this authored summary.")
        self.review("alpha")
        self.review("root")
        before = self.path.read_bytes()
        partial = deepcopy(self.index)
        del partial["nodes"]["a"]
        partial["nodes"]["alpha"]["children"] = []
        partial["projects"][0]["stats"] = {"bounded": True}
        with self.assertRaisesRegex(ValueError, "incomplete|partial"):
            self.store.refresh(partial)
        self.assertEqual(self.path.read_bytes(), before)
        restored = self.store.refresh(self.index)
        self.assertEqual(restored["nodes"]["a"]["summary"], "Do not discard this authored summary.")
        self.assertEqual(restored["nodes"]["a"]["summary_state"], "reviewed_current")
        self.assertEqual(self.store.import_delivery(self.delivery())["status"], "duplicate")
        changed = self.store.refresh(self.change_source("a"))
        self.assertTrue(changed["nodes"]["a"]["pending"])
        self.assertEqual(changed["nodes"]["a"]["summary"], "Do not discard this authored summary.")


if __name__ == "__main__":
    unittest.main()
