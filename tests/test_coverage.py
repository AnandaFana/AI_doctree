import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from doctree import coverage, markdown_protocol as protocol, portable


class CoverageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "project"
        self.root.mkdir()

    def directory(self, name):
        result = self.root / name
        result.mkdir(parents=True, exist_ok=True)
        return result

    def test_readonly_inventory_includes_deep_batches_and_ignored_business_outputs(self):
        for name in ("records/run1/card1/result", "history/2020/a", "output/batch", "overleaf_upload_v1/source", "build/generated", ".git/objects", ".doctree/lib", "node_modules/lib"):
            self.directory(name)
        (self.root / ".gitignore").write_text("records/\nhistory/\noutput/\nbuild/\n", encoding="utf-8")
        before = sorted(str(path.relative_to(self.root)) for path in self.root.rglob("*"))
        result = coverage.inspect(self.root)
        self.assertTrue(result["scope_complete"])
        paths = {row["directory"] for row in result["directories"]}
        self.assertIn("records/run1/card1/result", paths)
        self.assertIn("history/2020/a", paths)
        self.assertIn("build/generated", paths)
        self.assertNotIn("node_modules", paths)
        self.assertEqual({item["directory"] for item in result["excluded"]}, {".git", ".doctree", "node_modules"})
        self.assertTrue(any(row["depth"] == 4 for row in result["by_depth"]))
        self.assertFalse(result["scope_covered"])
        self.assertEqual(before, sorted(str(path.relative_to(self.root)) for path in self.root.rglob("*")))

    def test_selected_depth_is_deferred_not_failed_or_project_complete(self):
        self.directory("a/b/c")
        self.directory("other")
        plan = coverage.plan_cover(self.root, project_id="p", policy={"max_depth": 1})
        result = coverage.apply_cover(plan)
        self.assertTrue(result["coverage"]["scope_complete"])
        self.assertTrue(result["coverage"]["scope_covered"])
        self.assertFalse(result["coverage"]["project_complete"])
        check = coverage.inspect(self.root, policy={"max_depth": 1})
        self.assertEqual([item["directory"] for item in check["deferred"]], ["a/b"])
        self.assertEqual([item["directories"] for item in check["by_depth"]], [1, 2])
        self.assertFalse((self.root / "a/b/README.md").exists())

    def test_cover_preserves_bodies_and_custom_managed_entries_with_collision_safe_ids(self):
        original = b"\xef\xbb\xbf# Existing\r\n\r\nKeep original text.\r\n"
        (self.root / "README.md").write_bytes(original)
        custom = self.directory("custom")
        (custom / "GUIDE.md").write_text("# Custom owned document\n", encoding="utf-8")
        protocol.apply_plan(protocol.plan_sync(self.root, [{"directory": "custom", "entry": "GUIDE.md", "id": "p.custom", "title": "Custom", "purpose": "Original role"}], "p"))
        original_custom = protocol.semantic_text((custom / "GUIDE.md").read_text(encoding="utf-8"))
        self.directory("a b")
        self.directory("a-b")
        (self.root / "a b" / "result.json").write_text('{"status":"unknown"}', encoding="utf-8")
        planned = coverage.plan_cover(self.root, project_id="p", policy={"max_depth": None}, overrides={".": {"purpose": "Maintain the project entry"}})
        self.assertFalse((self.root / "a b/README.md").exists())
        result = coverage.apply_cover(planned)
        self.assertTrue(result["coverage"]["project_complete"])
        self.assertTrue((self.root / "README.md").read_bytes().endswith(original[3:]))
        self.assertTrue((self.root / "README.md").read_bytes().startswith(b"\xef\xbb\xbf"))
        self.assertFalse((custom / "README.md").exists())
        self.assertEqual(original_custom, protocol.semantic_text((custom / "GUIDE.md").read_text(encoding="utf-8")))
        nodes = protocol.discover_nodes(self.root)
        self.assertEqual(len(nodes), len({node["id"] for node in nodes}))
        generated = (self.root / "a b/README.md").read_text(encoding="utf-8")
        self.assertIn("具体职责、当前状态与重要结论待人或 Agent 阅读来源后补充", generated)
        self.assertIn("result.json", generated)
        self.assertEqual(coverage.plan_cover(self.root, policy={"max_depth": None})["changes"], [])

    def test_initial_cover_requires_an_explicit_scope(self):
        with self.assertRaisesRegex(ValueError, "显式选择"):
            coverage.plan_cover(self.root)
        self.assertFalse((self.root / "README.md").exists())
        for rules in ({"max_depth": -1}, {"max_depth": True}, {"unknown": 2}):
            with self.subTest(rules=rules), self.assertRaises(ValueError):
                coverage.inspect(self.root, policy=rules)

    def test_scope_does_not_remove_existing_deeper_nodes(self):
        self.directory("a/deep")
        protocol.apply_plan(protocol.plan_sync(self.root, [
            {"directory": ".", "id": "p", "purpose": "Root"},
            {"directory": "a/deep", "id": "p.deep", "purpose": "Existing deep responsibility"}], "p"))
        plan = coverage.plan_cover(self.root, policy={"max_depth": 0})
        coverage.apply_cover(plan)
        self.assertFalse((self.root / "a/README.md").exists())
        root_node = next(node for node in protocol.discover_nodes(self.root) if node["directory"] == ".")
        self.assertEqual(root_node["children"], ["p.deep"])
        self.assertIn("a/deep/README.md", (self.root / "README.md").read_text(encoding="utf-8"))

    def test_budget_limits_fail_closed_and_differ_from_selected_depth(self):
        self.directory("a/b")
        report = coverage.inspect(self.root, policy={"max_directories": 1})
        self.assertFalse(report["scope_complete"])
        self.assertFalse(report["project_complete"])
        with self.assertRaisesRegex(ValueError, "不完整"):
            coverage.plan_cover(self.root, policy={"max_depth": None, "max_directories": 1})
        (self.root / "ordinary.md").write_text("x" * 500, encoding="utf-8")
        byte_limited = coverage.inspect(self.root, policy={"max_total_bytes": 100})
        self.assertFalse(byte_limited["scope_complete"])
        self.assertFalse((self.root / "README.md").exists())

    def test_directory_change_after_preview_refuses_cover_without_document_writes(self):
        self.directory("a")
        plan = coverage.plan_cover(self.root, policy={"max_depth": 1})
        self.directory("new-arrival")
        with self.assertRaisesRegex(ValueError, "已过期"):
            coverage.apply_cover(plan)
        self.assertFalse((self.root / "README.md").exists())
        self.assertFalse((self.root / "a/README.md").exists())

    def test_malformed_header_reports_error_and_prevents_cover(self):
        (self.root / "README.md").write_text("<!-- doctree:node broken", encoding="utf-8")
        report = coverage.inspect(self.root)
        self.assertFalse(report["scope_complete"])
        self.assertEqual(report["directories"][0]["status"], "error")
        with self.assertRaises(ValueError):
            coverage.plan_cover(self.root, policy={"max_depth": 0})

    def test_custom_policy_excludes_are_visible_and_do_not_claim_project_complete(self):
        self.directory("private_work")
        rules = {"max_depth": None, "exclude_dirs": ["private_work"]}
        result = coverage.apply_cover(coverage.plan_cover(self.root, policy=rules))
        self.assertTrue(result["coverage"]["scope_covered"])
        self.assertFalse(result["coverage"]["project_complete"])
        self.assertFalse((self.root / "private_work/README.md").exists())

    def test_portable_cli_scope_preview_apply_and_depth_reminders_without_site_packages(self):
        self.directory("docs/deeper")
        portable.install(self.root)
        script = self.root / ".doctree/manage.py"
        def run(*args):
            return subprocess.run([sys.executable, "-I", "-S", "-X", "utf8", str(script), *args],
                                  cwd=self.root.parent, capture_output=True, text=True, encoding="utf-8", timeout=20)
        self.assertEqual(run("coverage").returncode, 0)
        self.assertEqual(run("coverage", "--check").returncode, 2)
        self.assertEqual(run("cover", "--apply").returncode, 2)
        preview = run("cover", "--depth", "1")
        self.assertEqual(preview.returncode, 0, preview.stderr)
        self.assertEqual(len(json.loads(preview.stdout)["changes"]), 2)
        self.assertFalse((self.root / "README.md").exists())
        self.assertEqual(run("coverage", "--depth", "1", "--check").returncode, 1)
        applied = run("cover", "--depth", "1", "--apply")
        self.assertEqual(applied.returncode, 0, applied.stderr)
        self.assertEqual(run("coverage", "--depth", "1", "--check").returncode, 0)
        self.assertEqual(run("coverage", "--all", "--check").returncode, 1)
        self.directory("docs/deeper/new")
        self.assertEqual(run("coverage", "--depth", "1", "--check").returncode, 0)
        self.directory("new-direct-child")
        self.assertEqual(run("coverage", "--depth", "1", "--check").returncode, 1)

    def test_portable_summary_keeps_scope_status_and_check_exit_code_without_directory_payload(self):
        self.directory("docs/deeper")
        portable.install(self.root)
        script = self.root / ".doctree/manage.py"
        result = subprocess.run([sys.executable, "-I", "-S", "-X", "utf8", str(script),
                                 "coverage", "--depth", "1", "--summary", "--check"],
                                cwd=self.root.parent, capture_output=True, text=True, encoding="utf-8", timeout=20)
        self.assertEqual(result.returncode, 1, result.stderr)
        report = json.loads(result.stdout)
        self.assertNotIn("directories", report)
        self.assertNotIn("excluded", report)
        self.assertEqual([row["depth"] for row in report["by_depth"]], [0, 1])
        self.assertTrue(report["scope_complete"])
        self.assertFalse(report["scope_covered"])
        self.assertFalse(report["project_complete"])

    def test_cover_and_explicit_sync_use_the_same_new_id_generator(self):
        for directory in ('a/b', 'a-b', 'a b'):
            self.directory(directory)
        covered = coverage.plan_cover(self.root, project_id='p', policy={'max_depth': None})
        explicit = protocol.plan_sync(self.root, [{'directory': row['directory']}
                                                  for row in covered['nodes']], 'p')
        self.assertEqual({node['directory']: node['id'] for node in covered['nodes']},
                         {node['directory']: node['id'] for node in explicit['nodes']})

    def test_excluded_non_utf8_file_fails_preflight_with_global_discovery_reason(self):
        archive = self.directory('archive')
        invalid = archive / 'old.md'
        invalid.write_bytes(b'# Old note\n\xff\xfe')
        policy = {'max_depth': 1, 'exclude_dirs': ['archive']}
        report = coverage.inspect(self.root, policy=policy)
        self.assertTrue(report['inventory_complete'])
        self.assertFalse(report['scope_complete'])
        self.assertFalse(report['protocol_discovery']['complete'])
        self.assertIn('archive/old.md', report['protocol_discovery']['errors'][0])
        self.assertIn('exclude_dirs 只排除新增治理范围', report['protocol_discovery']['errors'][0])
        compact = coverage.summary(report)
        self.assertFalse(compact['protocol_discovery']['complete'])
        with self.assertRaisesRegex(ValueError, 'archive/old.md'):
            coverage.plan_cover(self.root, policy=policy)
        self.assertEqual(invalid.read_bytes(), b'# Old note\n\xff\xfe')
        self.assertFalse((self.root / 'README.md').exists())

    def test_excluded_existing_nodes_stay_connected_and_keep_original_body(self):
        self.directory('archive/batch')
        self.directory('work')
        original = b'# Archived source\r\nKeep the original facts.\r\n'
        archived = self.root / 'archive/batch/README.md'
        archived.write_bytes(original)
        protocol.apply_plan(protocol.plan_sync(self.root, [
            {'directory': '.', 'id': 'p'}, {'directory': 'archive', 'id': 'p.archive'},
            {'directory': 'archive/batch', 'id': 'p.legacy-batch'}], 'p'))
        saved_archived = archived.read_bytes()
        policy = {'max_depth': 1, 'exclude_dirs': ['archive']}
        report = coverage.inspect(self.root, policy=policy)
        self.assertTrue(report['scope_complete'])
        self.assertTrue(report['protocol_discovery']['complete'])
        self.assertEqual(report['protocol_discovery']['outside_scope_node_count'], 2)
        plan = coverage.plan_cover(self.root, policy=policy)
        self.assertNotIn('archive', plan['coverage']['selected_directories'])
        coverage.apply_cover(plan)
        nodes = {node['id']: node for node in protocol.discover_nodes(self.root)}
        self.assertIn('p.archive', nodes['p']['children'])
        self.assertIn('p.legacy-batch', nodes['p.archive']['children'])
        self.assertEqual(archived.read_bytes(), saved_archived)
        self.assertTrue(archived.read_bytes().endswith(original))

    def test_change_to_excluded_existing_node_invalidates_saved_cover_plan(self):
        self.directory('archive')
        self.directory('work')
        protocol.apply_plan(protocol.plan_sync(self.root, [{'directory': '.', 'id': 'p'},
                                                           {'directory': 'archive', 'id': 'p.archive'}], 'p'))
        plan = coverage.plan_cover(self.root, policy={'max_depth': 1, 'exclude_dirs': ['archive']})
        archived = self.root / 'archive/README.md'
        archived.write_bytes(archived.read_bytes() + b'\nConcurrent archived note.\n')
        root_before = (self.root / 'README.md').read_bytes()
        with self.assertRaisesRegex(ValueError, '已过期'):
            coverage.apply_cover(plan)
        self.assertEqual((self.root / 'README.md').read_bytes(), root_before)
        self.assertFalse((self.root / 'work/README.md').exists())

    def test_global_discovery_budget_is_not_hidden_by_shallow_governance_scope(self):
        self.directory('archive/deep')
        report = coverage.inspect(self.root, policy={'max_depth': 0, 'max_directories': 1})
        self.assertTrue(report['inventory_complete'])
        self.assertFalse(report['protocol_discovery']['complete'])
        self.assertFalse(report['scope_complete'])
        with self.assertRaisesRegex(ValueError, '不完整'):
            coverage.plan_cover(self.root, policy={'max_depth': 0, 'max_directories': 1})
        self.assertFalse((self.root / 'README.md').exists())


if __name__ == "__main__":
    unittest.main()
