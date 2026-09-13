"""Read-only directory coverage and explicitly scoped Markdown onboarding.

Coverage is a project inventory, independent of viewer expansion limits. Missing
metadata is a decision to evaluate, not a claim that a project is defective.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import html
import json
import os
from pathlib import Path
import re
from urllib.parse import quote

from . import markdown_protocol as protocol


DEFAULT_EXCLUDES = sorted(protocol.DEFAULT_EXCLUDES)
DEFAULT_POLICY = {"schema": 1, "max_depth": None, "exclude_dirs": DEFAULT_EXCLUDES,
                  "max_scan_depth": 128, "max_directories": 20000,
                  "max_entries_per_directory": 20000, "max_markdown_files": 20000,
                  "max_file_bytes": 1_000_000, "max_total_bytes": 64_000_000}


def normalize_policy(policy=None):
    if policy is not None and not isinstance(policy, dict):
        raise ValueError("coverage policy 必须为 JSON 对象")
    value = dict(DEFAULT_POLICY)
    supplied = policy or {}
    unknown = set(supplied) - set(value)
    if unknown:
        raise ValueError("未知 coverage policy 字段：" + ", ".join(sorted(unknown)))
    value.update(supplied)
    if value["schema"] != 1:
        raise ValueError("不支持的 coverage policy schema")
    depth = value["max_depth"]
    if depth is not None and (type(depth) is not int or depth < 0):
        raise ValueError("max_depth 必须为非负整数或 null（全部目录）")
    for name in ("max_scan_depth", "max_directories", "max_entries_per_directory", "max_markdown_files", "max_file_bytes", "max_total_bytes"):
        if type(value[name]) is not int or value[name] < 1:
            raise ValueError(f"{name} 必须为正整数")
    excludes = value["exclude_dirs"]
    if not isinstance(excludes, list) or not all(isinstance(item, str) and item and not any(c in item for c in "/\\:") and item not in (".", "..") for item in excludes):
        raise ValueError("exclude_dirs 必须为目录名字符串数组")
    # Tool internals and real dependency/cache exclusions are common to sync.
    value["exclude_dirs"] = sorted({name.casefold() for name in DEFAULT_EXCLUDES + excludes})
    return value


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _discovery_options(rules):
    """Scope exclusions select new work, never hide existing navigation nodes."""
    return {"max_depth": rules["max_scan_depth"], "max_files": rules["max_markdown_files"],
            "max_directories": rules["max_directories"], "max_file_bytes": rules["max_file_bytes"],
            "max_total_bytes": rules["max_total_bytes"]}


def inspect(root, *, policy=None):
    """Inspect all normal directories in the selected depth without mutations.

    scope_complete requires selected inventory and global protocol discovery to
    succeed; scope_covered also requires managed documentation. User exclusions
    select new governance work and never remove existing nodes from discovery.
    """
    root = protocol._root(root)
    rules = normalize_policy(policy)
    directories, excluded, deferred, errors = [], [], [], []
    depth_counts = {}
    markdown_count = total_bytes = 0
    seen_ids = {}
    budget_exhausted = False
    stack = [(root, 0)]
    excluded_names = set(rules["exclude_dirs"])
    while stack:
        folder, depth = stack.pop()
        relative = folder.relative_to(root).as_posix()
        if len(directories) >= rules["max_directories"]:
            errors.append({"directory": relative, "reason": "目录数量超过预算", "remaining_roots": len(stack) + 1})
            break
        files, children, row_errors = [], [], []
        try:
            protocol._check_ancestry(folder)
            with os.scandir(folder) as entries:
                for index, entry in enumerate(entries):
                    if index >= rules["max_entries_per_directory"]:
                        row_errors.append("目录条目数量超过预算")
                        break
                    child = Path(entry.path)
                    child_relative = child.relative_to(root).as_posix()
                    if protocol._is_link(child):
                        excluded.append({"directory": child_relative, "reason": "不跟随符号链接或目录联接"})
                    elif entry.is_dir(follow_symlinks=False):
                        if entry.name.casefold() in excluded_names:
                            reason = "默认工具、依赖或缓存排除" if entry.name.casefold() in {name.casefold() for name in DEFAULT_EXCLUDES} else "本次 policy 指定排除"
                            excluded.append({"directory": child_relative, "reason": reason, "rule": entry.name.casefold()})
                        else:
                            children.append(child)
                    elif entry.is_file(follow_symlinks=False):
                        files.append(child)
        except (OSError, ValueError) as exc:
            row_errors.append(str(exc))
        files.sort(key=lambda path: path.name.casefold())
        children.sort(key=lambda path: path.name.casefold())
        documents = []
        conventional = [path for path in files if path.name.casefold() in ("readme.md", "doctree.md")]
        conventional.sort(key=lambda path: (path.name.casefold() != "readme.md", path.name != "README.md", path.name))
        content_hashes = {}
        for path in files:
            if path.suffix.casefold() != ".md":
                continue
            try:
                info = path.stat()
                markdown_count += 1
                if markdown_count > rules["max_markdown_files"]:
                    budget_exhausted = True
                    raise ValueError("Markdown 文件数量超过预算")
                if info.st_size > rules["max_file_bytes"]:
                    raise ValueError("Markdown 文件大小超过预算")
                if total_bytes + info.st_size > rules["max_total_bytes"]:
                    budget_exhausted = True
                    raise ValueError("Markdown 读取总量超过预算")
                if info.st_nlink > 1:
                    raise ValueError("Markdown 为多硬链接文件，不能安全接入")
                remaining = min(rules["max_file_bytes"], rules["max_total_bytes"] - total_bytes)
                with path.open("rb") as stream:
                    raw = stream.read(remaining + 1)
                total_bytes += len(raw)
                if len(raw) > rules["max_file_bytes"] or total_bytes > rules["max_total_bytes"]:
                    budget_exhausted = total_bytes > rules["max_total_bytes"]
                    raise ValueError("读取期间 Markdown 超过预算")
                content_hashes[path.name] = hashlib.sha256(raw).hexdigest()
                metadata = protocol.parse_document(raw.decode("utf-8"))
                if metadata:
                    documents.append((path, metadata))
                    if metadata["id"] in seen_ids:
                        row_errors.append(f"重复节点 ID：{metadata['id']}（另见 {seen_ids[metadata['id']]}）")
                    seen_ids[metadata["id"]] = path.relative_to(root).as_posix()
            except (OSError, ValueError) as exc:
                row_errors.append(f"{path.name}: {exc}")
                if budget_exhausted:
                    break
        if len(documents) > 1:
            row_errors.append("一个目录存在多份已接入节点文档")
        metadata = documents[0][1] if documents else None
        primary = documents[0][0] if documents else (conventional[0] if conventional else None)
        status = "error" if row_errors else "managed" if metadata else "unmanaged" if primary else "missing"
        row = {"directory": relative, "depth": depth, "status": status,
               "entry": primary.name if primary else None,
               "path": primary.relative_to(root).as_posix() if primary else None,
               "node_id": metadata["id"] if metadata else None,
               "title": metadata["title"] if metadata else folder.name,
               "purpose": metadata.get("purpose", "") if metadata else "",
               "has_readme": any(path.name.casefold() == "readme.md" for path in conventional),
               "file_count": len(files), "child_directory_count": len(children),
               "files": [path.name for path in files[:40]], "files_list_partial": len(files) > 40,
               "inventory_sha256": _digest({"files": [path.name for path in files],
                                             "directories": [path.name for path in children],
                                             "markdown": content_hashes})}
        directories.append(row)
        count = depth_counts.setdefault(depth, Counter())
        count["directories"] += 1
        count[status] += 1
        if row_errors:
            errors.extend({"directory": relative, "reason": message} for message in row_errors)
        if budget_exhausted:
            break
        if rules["max_depth"] is not None and depth >= rules["max_depth"]:
            deferred.extend({"directory": path.relative_to(root).as_posix(), "depth": depth + 1,
                             "reason": "超出本次选择的治理深度，后续层级未扫描"} for path in children)
        elif depth >= rules["max_scan_depth"] and children:
            errors.append({"directory": relative, "reason": "达到安全遍历深度预算，未完成范围检查"})
        else:
            stack.extend((path, depth + 1) for path in reversed(children))
    counts = {name: sum(row["status"] == name for row in directories) for name in ("managed", "unmanaged", "missing", "error")}
    counts.update(directories=len(directories), excluded_roots=len(excluded), deferred_roots=len(deferred),
                  readme_present=sum(row["has_readme"] for row in directories), markdown_files=markdown_count,
                  markdown_bytes=total_bytes)
    inventory_complete = not errors
    discovery_options = _discovery_options(rules)
    protocol_discovery = {"complete": False, "scope": "project-wide existing-node discovery",
                          "policy": discovery_options, "managed_nodes": None,
                          "outside_scope_node_count": None, "snapshot_sha256": None, "errors": []}
    try:
        discovered = protocol.discover_nodes(root, **discovery_options)
        selected_directories = {row["directory"] for row in directories}
        protocol_discovery.update(
            complete=True, managed_nodes=len(discovered),
            outside_scope_node_count=sum(node["directory"] not in selected_directories for node in discovered),
            snapshot_sha256=_digest([{key: node[key] for key in ("id", "path", "sha256", "parent", "children")}
                                     for node in discovered]))
    except (OSError, ValueError) as exc:
        reason = (f"全局节点发现失败：{exc}。exclude_dirs 只排除新增治理范围，不能绕过既有父子导航发现；"
                  "请根据该路径核对来源，是否处理编码或重新选择项目根由用户决定。")
        protocol_discovery["errors"].append(reason)
        errors.append({"directory": ".", "stage": "protocol_discovery", "reason": reason})
    scope_complete = inventory_complete and protocol_discovery["complete"]
    scope_covered = scope_complete and counts["managed"] == counts["directories"]
    custom_exclusions = set(rules["exclude_dirs"]) - {name.casefold() for name in DEFAULT_EXCLUDES}
    omitted_custom = any(item.get("rule") in custom_exclusions for item in excluded)
    return {"schema": 1, "root": str(root), "policy": rules, "scope_complete": scope_complete,
            "inventory_complete": inventory_complete, "protocol_discovery": protocol_discovery,
            "scope_covered": scope_covered, "project_complete": scope_covered and not deferred and not omitted_custom,
            "counts": counts, "by_depth": [{"depth": depth, **{key: count[key] for key in ("directories", "managed", "unmanaged", "missing", "error")}} for depth, count in sorted(depth_counts.items())],
            "directories": directories, "excluded": excluded, "deferred": deferred, "errors": errors,
            "snapshot_sha256": _digest({"inventory": [{key: row[key] for key in ("directory", "status", "entry", "node_id", "inventory_sha256")} for row in directories],
                                         "protocol_discovery": protocol_discovery["snapshot_sha256"]}),
            "notice": "未接入目录是待评估范围，不表示项目有问题。治理深度与 exclude_dirs 只选择新增工作范围；全局既有节点仍参与父子导航发现。页面展开深度不决定治理范围。"}


def summary(report):
    result = {key: report[key] for key in ("scope_complete", "scope_covered", "project_complete", "inventory_complete", "counts", "by_depth", "notice")}
    result["protocol_discovery"] = {key: report["protocol_discovery"][key] for key in
                                    ("complete", "scope", "managed_nodes", "outside_scope_node_count", "errors")}
    return result


def _label(value):
    value = html.escape(str(value), quote=False).replace("\r", " ").replace("\n", " ")
    return re.sub(r"([\\`*_[\]#])", r"\\\1", value)


def _initial_body(row):
    # These statements are an inventory, not a guessed scientific responsibility.
    lines = [f"# {_label(row['title'])}", "", f"目录位置：{_label(row['directory'])}。", "",
             "本页是目录导航入口。具体职责、当前状态与重要结论待人或 Agent 阅读来源后补充。工作范围与限制先沿上级 README 核对；文件存在不表示执行成功或业务、科学验收。", "",
             f"接入前直接包含 {row['file_count']} 个文件、{row['child_directory_count']} 个普通子目录。子目录入口由页首导航维护。", ""]
    if row["files"]:
        lines.extend(["## 接入时文件入口", ""])
        lines.extend(f"- [{_label(name)}]({quote(name, safe='.-_~')})" for name in row["files"])
        if row["files_list_partial"]:
            lines.append("\n上面只列出前 40 个文件；完整内容以实际目录为准。")
        lines.append("")
    lines.extend(["## 更新约定", "", "完成本目录工作后，按实际依据更新本页；影响上层范围、结论或下一步时核对父节点。不要从目录名、模板或退出码自动推断结果通过。", ""])
    return "\n".join(lines)


def plan_cover(root, *, project_id=None, policy=None, overrides=None):
    """Plan onboarding only after depth or explicit all-directory scope is chosen."""
    if not isinstance(policy, dict) or "max_depth" not in policy:
        raise ValueError("接入前请显式选择 max_depth（0 为根、1 为直接子目录，null 为全部）")
    report = inspect(root, policy=policy)
    if not report["scope_complete"]:
        raise ValueError("覆盖发现不完整，拒绝生成写入计划：" + "; ".join(item["reason"] for item in report["errors"][:5]))
    overrides = overrides or {}
    if not isinstance(overrides, dict):
        raise ValueError("overrides 必须按相对目录提供对象")
    known = {row["directory"] for row in report["directories"]}
    if set(overrides) - known:
        raise ValueError("职责覆盖包含本次范围之外的目录")
    root_row = next(row for row in report["directories"] if row["directory"] == ".")
    project_id = root_row["node_id"] or project_id
    if not project_id:
        slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", Path(report["root"]).name).strip("-._")
        project_id = slug[:120] or "project." + hashlib.sha256(Path(report["root"]).name.encode()).hexdigest()[:12]
    if not isinstance(project_id, str) or not protocol.ID_PATTERN.fullmatch(project_id):
        raise ValueError("project_id 格式无效")
    selections = []
    for row in report["directories"]:
        override = overrides.get(row["directory"], {})
        if not isinstance(override, dict) or set(override) - {"title", "purpose", "initial_body"}:
            raise ValueError("每个 override 只允许 title、purpose、initial_body")
        if row["status"] == "managed" and not override:
            continue
        item = {"directory": row["directory"], "entry": row["entry"] or "README.md"}
        if row["status"] != "managed":
            item["id"] = protocol.generated_node_id(project_id, row["directory"])
            item["title"] = row["title"].replace("\r", " ").replace("\n", " ")
            item["purpose"] = "本目录的导航与说明入口；具体职责待人或 Agent 根据来源补充。"
            if row["status"] == "missing":
                item["initial_body"] = _initial_body(row)
        item.update(override)
        selections.append(item)
    rules = report["policy"]
    # Discovery remains project-wide so out-of-scope existing nodes keep their
    # relationships. Scope filters only new selections, never the existing tree.
    discovery = _discovery_options(rules)
    plan = protocol.plan_sync(root, selections, project_id, discovery_options=discovery)
    plan["coverage"] = {**summary(report), "policy": rules, "snapshot_sha256": report["snapshot_sha256"],
                        "selected_directories": [item["directory"] for item in selections],
                        "deferred": report["deferred"], "excluded": report["excluded"]}
    return plan


def apply_cover(plan):
    """Recheck coverage inventory, then reuse versioned protocol application."""
    saved = plan.get("coverage")
    if not isinstance(saved, dict) or "policy" not in saved:
        raise ValueError("缺少覆盖计划的范围与输入快照")
    current = inspect(plan["root"], policy=saved["policy"])
    if not current["scope_complete"] or current["snapshot_sha256"] != saved.get("snapshot_sha256"):
        raise ValueError("覆盖计划已过期：目录或文件发生变化，请重新检查范围并生成计划")
    result = protocol.apply_plan(plan)
    result["coverage"] = summary(inspect(plan["root"], policy=saved["policy"]))
    return result
