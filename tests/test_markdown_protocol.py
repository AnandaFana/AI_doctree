import json
from contextlib import redirect_stdout
from io import StringIO
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from doctree.markdown_protocol import (NAV_START, NAV_END, PURPOSE_START,
                                       SyncApplyError, _sync_lock, apply_plan, discover_nodes,
                                       parse_document, plan_sync, semantic_text)


class MarkdownProtocolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'project'
        self.root.mkdir()
        self.backups = Path(self.temp.name) / 'backups'

    def write(self, relative, value=b'# Original\n\nKeep this body.\n'):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value.encode('utf-8') if isinstance(value, str) else value)
        return path

    def sync(self, selections):
        plan = plan_sync(self.root, selections, 'demo')
        report = apply_plan(plan, self.backups)
        return plan, report

    def test_bom_mixed_newlines_and_original_body_bytes_preserved(self):
        raw = b'\xef\xbb\xbf# Original\r\n\r\nMixed\n  keep trailing spaces  \r\n'
        path = self.write('README.md', raw)
        plan, report = self.sync([{'directory': '.', 'title': '项目', 'purpose': '维护完整文档。'}])
        result = path.read_bytes()
        self.assertTrue(result.startswith(b'\xef\xbb\xbf<!-- doctree:node'))
        self.assertTrue(result.endswith(raw[3:]))
        self.assertEqual((Path(report['backup_dir']) / 'originals/README.md').read_bytes(), raw)
        self.assertEqual(parse_document(result.decode('utf-8'))['purpose'], '维护完整文档。')
        self.assertEqual(plan['changes'][0]['expected_sha256'], plan['inputs'][0]['expected_sha256'])

    def test_unusual_source_newlines_do_not_spread_to_new_header(self):
        raw = b'# Source\r\r\nLegacy text\r\r\n'
        path = self.write('README.md', raw)
        self.sync([{'directory': '.'}])
        after = path.read_bytes()
        prefix = after[:-len(raw)]
        self.assertIn(b'\r\n', prefix)
        self.assertNotIn(b'\r\r\n', prefix)
        self.assertTrue(after.endswith(raw))

    def test_resync_is_idempotent_and_preserves_manually_edited_purpose(self):
        path = self.write('README.md')
        self.sync([{'directory': '.', 'title': 'Project', 'purpose': 'First purpose.'}])
        path.write_bytes(path.read_bytes().replace(b'First purpose.', '人工写的新职责。'.encode('utf-8')))
        before = path.read_bytes()
        plan = plan_sync(self.root, [], 'demo')
        self.assertEqual(plan['changes'], [])
        self.assertEqual(apply_plan(plan, self.backups)['status'], 'unchanged')
        self.assertEqual(path.read_bytes(), before)
        updated = plan_sync(self.root, [{'directory': '.', 'purpose': 'Explicit new purpose.'}], 'demo')
        apply_plan(updated, self.backups)
        self.assertEqual(parse_document(path.read_bytes().decode())['purpose'], 'Explicit new purpose.')

    def test_stale_input_rejects_entire_batch_before_any_document_writes(self):
        first = self.write('README.md')
        second = self.write('child/README.md')
        plan = plan_sync(self.root, [{'directory': '.'}, {'directory': 'child'}], 'demo')
        second.write_bytes(b'Concurrent edit\n')
        with self.assertRaisesRegex(ValueError, '过期'):
            apply_plan(plan, self.backups)
        self.assertEqual(first.read_bytes(), b'# Original\n\nKeep this body.\n')
        self.assertEqual(second.read_bytes(), b'Concurrent edit\n')
        self.assertFalse(self.backups.exists())

    def test_custom_filename_nearest_ancestor_and_url_escaping(self):
        self.write('README.md')
        child = self.write('child space/inner/NODE (中文版).md')
        plan, _ = self.sync([{'directory': '.', 'title': 'Root'},
                             {'directory': 'child space/inner', 'entry': child.name, 'title': 'Child [x]'}])
        root_text = (self.root / 'README.md').read_text(encoding='utf-8')
        self.assertIn('child%20space/inner/NODE%20%28%E4%B8%AD%E6%96%87%E7%89%88%29.md', root_text)
        self.assertIn('[Child \\[x\\]]', root_text)
        self.assertIn('[Root](../../README.md)', child.read_text(encoding='utf-8'))
        found = discover_nodes(self.root)
        self.assertEqual(len(found), 2)
        by_path = {node['path']: node for node in found}
        self.assertEqual(by_path['child space/inner/NODE (中文版).md']['parent'], 'demo')
        self.assertEqual(plan_sync(self.root, [], 'demo')['changes'], [])

    def test_directory_move_keeps_id_and_rebuilds_all_links(self):
        self.write('README.md')
        self.write('child/README.md')
        self.sync([{'directory': '.'}, {'directory': 'child', 'id': 'demo.stable-child'}])
        (self.root / 'child').rename(self.root / 'moved')
        plan, report = self.sync([])
        moved = next(node for node in discover_nodes(self.root) if node['directory'] == 'moved')
        self.assertEqual(moved['id'], 'demo.stable-child')
        root_text = (self.root / 'README.md').read_text(encoding='utf-8')
        self.assertIn('(moved/README.md)', root_text)
        self.assertNotIn('(child/README.md)', root_text)
        self.assertEqual(report['written'], ['README.md'])

    def test_removing_child_node_rebuilds_parent_navigation(self):
        self.write('README.md')
        child = self.write('child/README.md')
        self.sync([{'directory': '.'}, {'directory': 'child'}])
        child.write_bytes(b'# No longer managed\n')
        self.sync([])
        self.assertNotIn('(child/README.md)', (self.root / 'README.md').read_text(encoding='utf-8'))

    def test_new_managed_document_after_planning_invalidates_plan(self):
        self.write('README.md')
        self.write('child/README.md')
        self.sync([{'directory': '.'}])
        old = plan_sync(self.root, [{'directory': '.', 'title': 'Changed'}], 'demo')
        self.sync([{'directory': 'child'}])
        with self.assertRaisesRegex(ValueError, '集合发生变化'):
            apply_plan(old, self.backups)

    def test_navigation_is_excluded_but_purpose_and_body_are_semantic(self):
        self.write('README.md')
        self.sync([{'directory': '.', 'purpose': 'Human purpose'}])
        text = (self.root / 'README.md').read_text(encoding='utf-8')
        changed_nav = text.replace('无（项目入口）', '导航已更新')
        self.assertEqual(semantic_text(text), semantic_text(changed_nav))
        self.assertNotEqual(semantic_text(text), semantic_text(text.replace('Human purpose', 'Revised purpose')))
        body_example = '# Usage example\n\n' + text
        self.assertIsNone(parse_document(body_example))
        self.assertEqual(semantic_text(body_example), body_example)

    def test_malformed_duplicate_and_misordered_markers_are_rejected(self):
        self.write('README.md')
        self.sync([{'directory': '.'}])
        text = (self.root / 'README.md').read_text(encoding='utf-8')
        invalid = [text.replace(NAV_END, ''), text.replace(NAV_START, NAV_START + '\n' + NAV_START),
                   text.replace(PURPOSE_START, NAV_START), text.replace(NAV_END, NAV_END + '\n' + NAV_START),
                   '<!-- doctree:node broken -->\n', '<!-- doctree:node {"schema":99} -->']
        for value in invalid:
            with self.subTest(value=value[:90]), self.assertRaises(ValueError):
                parse_document(value)

    def test_path_escape_and_protocol_internal_paths_rejected(self):
        self.write('README.md')
        for relative in ('../outside', '.git', '.doctree', 'C:/outside'):
            with self.subTest(relative=relative), self.assertRaises(ValueError):
                plan_sync(self.root, [{'directory': relative}], 'demo')
        for entry in ('../outside.md', 'a/README.md', 'a\\README.md', 'secret:stream.md'):
            with self.subTest(entry=entry), self.assertRaises(ValueError):
                plan_sync(self.root, [{'directory': '.', 'entry': entry}], 'demo')

    def test_hardlink_rejected_without_touching_either_name(self):
        source = self.write('README.md')
        target = Path(self.temp.name) / 'outside.md'
        try:
            os.link(source, target)
        except OSError as exc:
            self.skipTest(f'Platform cannot create hardlinks: {exc}')
        with self.assertRaisesRegex(ValueError, '硬链接'):
            plan_sync(self.root, [{'directory': '.'}], 'demo')
        self.assertEqual(source.read_bytes(), target.read_bytes())

    def test_symbolic_directory_selection_is_rejected(self):
        outside = Path(self.temp.name) / 'outside'
        outside.mkdir()
        link = self.root / 'linked'
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f'Platform cannot create symlinks: {exc}')
        with self.assertRaisesRegex(ValueError, '链接|重解析'):
            plan_sync(self.root, [{'directory': 'linked'}], 'demo')

    def test_modified_plan_cannot_replace_original_body(self):
        path = self.write('README.md')
        plan = plan_sync(self.root, [{'directory': '.'}], 'demo')
        plan['changes'][0]['after'] = plan['changes'][0]['after'].replace('Keep this body.', 'Deleted source facts.')
        with self.assertRaisesRegex(ValueError, '正文'):
            apply_plan(plan, self.backups)
        self.assertEqual(path.read_bytes(), b'# Original\n\nKeep this body.\n')

    def test_partial_write_preserves_originals_and_journal(self):
        import doctree.markdown_protocol as module
        self.write('README.md')
        self.write('child/README.md')
        plan = plan_sync(self.root, [{'directory': '.'}, {'directory': 'child'}], 'demo')
        original_atomic = module._atomic_bytes

        def fail_second_source(path, data, mode=None):
            if path == self.root / 'child/README.md':
                raise OSError('simulated disk failure')
            return original_atomic(path, data, mode)

        with patch.object(module, '_atomic_bytes', side_effect=fail_second_source):
            with self.assertRaises(SyncApplyError) as failure:
                apply_plan(plan, self.backups)
        report = failure.exception.report
        self.assertEqual(report['status'], 'partial')
        self.assertEqual(report['written'], ['README.md'])
        journal = json.loads(Path(report['journal']).read_text(encoding='utf-8'))
        self.assertEqual(journal['status'], 'partial')
        self.assertEqual([item['status'] for item in journal['files']], ['written', 'pending'])
        for relative in ('README.md', 'child/README.md'):
            backup = Path(report['backup_dir']) / 'originals' / relative
            self.assertEqual(backup.read_bytes(), b'# Original\n\nKeep this body.\n')
        self.assertEqual((self.root / 'child/README.md').read_bytes(), b'# Original\n\nKeep this body.\n')

    def test_budget_exhaustion_rejects_incomplete_discovery(self):
        self.write('README.md')
        self.write('child/README.md')
        with self.assertRaisesRegex(ValueError, '预算'):
            discover_nodes(self.root, max_files=1)
        with self.assertRaisesRegex(ValueError, '深度'):
            discover_nodes(self.root, max_depth=0)

    def test_cross_process_lock_blocks_writer_then_releases(self):
        path = self.write('README.md')
        before = path.read_bytes()
        script = '''from pathlib import Path
import sys
from doctree.markdown_protocol import plan_sync, apply_plan
plan = plan_sync(Path(sys.argv[1]), [{'directory': '.'}], 'demo')
try:
    report = apply_plan(plan, Path(sys.argv[2]), lock_timeout=0.15)
except ValueError as exc:
    print(str(exc))
    raise SystemExit(7)
print(report['status'])
'''
        command = [sys.executable, '-X', 'utf8', '-c', script, str(self.root), str(self.backups)]
        with _sync_lock(self.root, 0.15):
            blocked = subprocess.run(command, capture_output=True, encoding='utf-8', timeout=15)
            self.assertEqual(blocked.returncode, 7, blocked.stderr)
            self.assertIn('同步锁等待超时', blocked.stdout)
            self.assertEqual(path.read_bytes(), before)
        completed = subprocess.run(command, capture_output=True, encoding='utf-8', timeout=15)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn('applied', completed.stdout)
        self.assertIsNotNone(parse_document(path.read_bytes().decode('utf-8')))

    def test_hardlinked_lock_is_rejected_before_any_document_write(self):
        path = self.write('README.md')
        lockdir = self.root / '.doctree'
        lockdir.mkdir()
        lock = lockdir / 'sync.lock'
        lock.write_bytes(b'0')
        try:
            os.link(lock, Path(self.temp.name) / 'outside-lock')
        except OSError as exc:
            self.skipTest(f'Platform cannot create hardlinks: {exc}')
        plan = plan_sync(self.root, [{'directory': '.'}], 'demo')
        with self.assertRaisesRegex(ValueError, '同步锁.*硬链接'):
            apply_plan(plan, self.backups)
        self.assertEqual(path.read_bytes(), b'# Original\n\nKeep this body.\n')

    def test_history_batch_and_output_directories_are_discovered_and_linked(self):
        directories = ['.', 'records', 'records/batch-001', 'runs', 'history',
                       'history/older', 'build', 'dist', 'overleaf_upload_2026']
        for directory in directories:
            (self.root / directory).mkdir(parents=True, exist_ok=True)
        (self.root / 'node_modules').mkdir()
        (self.root / 'node_modules' / 'broken.md').write_bytes(b'\xff\xfeIgnored dependency')
        plan, _ = self.sync([{'directory': directory} for directory in directories])
        found = discover_nodes(self.root)
        self.assertEqual({node['directory'] for node in found}, set(directories))
        self.assertIn('(batch-001/README.md)', (self.root / 'records/README.md').read_text(encoding='utf-8'))
        self.assertEqual(plan_sync(self.root, [], 'demo')['changes'], [])
        with self.assertRaisesRegex(ValueError, '排除范围'):
            plan_sync(self.root, [{'directory': 'node_modules'}], 'demo')

    def test_selected_depth_does_not_disconnect_existing_deeper_managed_nodes(self):
        self.write('README.md')
        self.write('work/deep/README.md')
        self.sync([{'directory': '.'}, {'directory': 'work/deep', 'id': 'demo.deep'}])
        self.sync([{'directory': 'work', 'id': 'demo.work'}])
        found = {node['id']: node for node in discover_nodes(self.root)}
        self.assertEqual(found['demo.deep']['parent'], 'demo.work')
        self.assertIn('demo.deep', found['demo.work']['children'])

    def test_initial_body_is_allowed_only_for_new_document_and_then_preserved(self):
        (self.root / 'fresh').mkdir()
        initial = '# 新目录\r\n\r\n待人工填写结论；当前仅列出文件。\n'
        plan = plan_sync(self.root, [{'directory': 'fresh', 'initial_body': initial}], 'demo')
        apply_plan(plan, self.backups)
        path = self.root / 'fresh' / 'README.md'
        self.assertTrue(path.read_bytes().endswith(initial.encode('utf-8')))
        self.assertEqual(plan_sync(self.root, [], 'demo')['changes'], [])
        with self.assertRaisesRegex(ValueError, '尚不存在'):
            plan_sync(self.root, [{'directory': 'fresh', 'initial_body': 'replace it'}], 'demo')

    def test_custom_discovery_policy_is_persisted_and_apply_refuses_new_budget_overflow(self):
        self.write('README.md')
        self.write('child/README.md')
        plan = plan_sync(self.root, [{'directory': '.'}], 'demo', discovery_options={'max_directories': 2})
        self.assertEqual(plan['discovery_options']['max_directories'], 2)
        (self.root / 'new-directory').mkdir()
        with self.assertRaisesRegex(ValueError, '目录发现超过预算'):
            apply_plan(plan, self.backups)
        self.assertIsNone(parse_document((self.root / 'README.md').read_text(encoding='utf-8')))
        with self.assertRaisesRegex(ValueError, '总字节'):
            plan_sync(self.root, [{'directory': '.'}], 'demo', discovery_options={'max_total_bytes': 100})
        with self.assertRaisesRegex(ValueError, '同步结果超过 Markdown 文件'):
            plan_sync(self.root, [{'directory': 'new-directory'}], 'demo', discovery_options={'max_files': 2})

    def test_directory_access_failure_cannot_silently_truncate_sync_discovery(self):
        import doctree.markdown_protocol as module

        def unreadable_walk(root, **kwargs):
            kwargs['onerror'](PermissionError('unreadable subtree'))
            return iter(())

        with patch.object(module.os, 'walk', side_effect=unreadable_walk):
            with self.assertRaisesRegex(ValueError, '拒绝生成部分导航'):
                plan_sync(self.root, [], 'demo')

    def test_generated_ids_distinguish_path_separators_and_preserve_existing_ids(self):
        from doctree.markdown_protocol import generated_node_id
        for directory in ('a/b', 'a-b', 'a b'):
            (self.root / directory).mkdir(parents=True, exist_ok=True)
        plan = plan_sync(self.root, [{'directory': directory} for directory in ('a/b', 'a-b', 'a b')], 'demo')
        ids = {node['directory']: node['id'] for node in plan['nodes']}
        self.assertEqual(len(set(ids.values())), 3)
        self.assertEqual(ids['a/b'], generated_node_id('demo', 'a\\b'))
        apply_plan(plan, self.backups)
        self.assertEqual(plan_sync(self.root, [], 'demo')['changes'], [])
        self.assertNotEqual(generated_node_id('x' * 130 + 'one', 'a'),
                            generated_node_id('x' * 130 + 'two', 'a'))
        self.assertLessEqual(len(generated_node_id('x' * 160, 'deep/path')), 160)
        (self.root / 'legacy').mkdir()
        self.sync([{'directory': 'legacy', 'id': 'demo.old-path-style'}])
        existing = next(node for node in plan_sync(self.root, [{'directory': 'legacy'}], 'demo')['nodes']
                        if node['directory'] == 'legacy')
        self.assertEqual(existing['id'], 'demo.old-path-style')

    def test_annotate_cli_can_register_both_nested_and_hyphenated_directories(self):
        from doctree.portable import main
        for directory in ('a/b', 'a-b'):
            (self.root / directory).mkdir(parents=True, exist_ok=True)
            with redirect_stdout(StringIO()):
                code = main(['--root', str(self.root), 'annotate', '--directory', directory])
            self.assertEqual(code, 0)
        nodes = discover_nodes(self.root)
        self.assertEqual({node['directory'] for node in nodes}, {'a/b', 'a-b'})
        self.assertEqual(len({node['id'] for node in nodes}), 2)


if __name__ == '__main__':
    unittest.main()
