import json
import stat
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase, mock

from doctree.foldertree import _link, build_tree, context, read_source, open_folder, project_path
from doctree.markdown_protocol import plan_sync, apply_plan


class FolderTreeTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'docs').mkdir()
        (self.root / 'records').mkdir()
        (self.root / 'records' / 'thousands-of-runs').mkdir()
        (self.root / 'README.md').write_text('# Original project\nKeep this context.', encoding='utf-8')
        self.projects = [{'id': 'p', 'title': 'Project', 'root': str(self.root)}]
        selections = [{'directory': '.', 'id': 'p.root', 'title': 'Root', 'purpose': 'Own the project'},
                      {'directory': 'docs', 'entry': 'GUIDE.md', 'id': 'p.docs', 'title': 'Docs', 'purpose': 'Own documents'}]
        self.plan = plan_sync(self.root, selections, 'p')
        apply_plan(self.plan)

    def test_markdown_reconstructs_tree_without_legacy_state_or_catalog(self):
        (self.root / '.doctree' / 'state.json').write_text('not valid json', encoding='utf-8')
        tree = build_tree(self.projects)
        self.assertEqual(tree['projects'][0]['managed_count'], 2)
        self.assertEqual(tree['nodes']['p.docs']['parent'], 'p.root')
        self.assertEqual(tree['nodes']['p.docs']['purpose'], 'Own documents')
        (self.root / '.doctree' / 'state.json').unlink()
        self.assertEqual(tree['nodes'], build_tree(self.projects)['nodes'])

    def test_bulk_directory_stays_accessible_without_run_expansion(self):
        tree = build_tree(self.projects)
        record = next(n for n in tree['nodes'].values() if n['directory'] == 'records')
        self.assertFalse(record['managed'])
        self.assertTrue(record['collapsed_reason'])
        self.assertEqual(record['children'], [])
        self.assertFalse(any(n['directory'].startswith('records/') for n in tree['nodes'].values()))
        self.assertFalse(any('.doctree' in n['directory'] for n in tree['nodes'].values()))

    def test_readme_presence_is_distinct_from_custom_protocol_and_requirement(self):
        tree = build_tree(self.projects)
        self.assertTrue(tree['nodes']['p.root']['has_readme'])
        self.assertFalse(tree['nodes']['p.docs']['has_readme'])
        self.assertTrue(tree['nodes']['p.docs']['readme_available'])
        self.assertFalse(tree['nodes']['p.docs']['readme_required'])

    def test_context_and_custom_entry_are_live_markdown(self):
        result = context(self.projects, 'p', 'docs')
        self.assertEqual(result['target']['entry'], 'docs/GUIDE.md')
        self.assertEqual(result['ancestors'][0]['purpose'], 'Own the project')
        self.assertEqual(result['ancestors'][0]['entry'], 'README.md')
        self.assertNotIn('document', result['ancestors'][0])
        expanded = context(self.projects, 'p', 'docs', body_scope='all')
        self.assertIn('Keep this context.', expanded['ancestors'][0]['document']['content'])
        guide = self.root / 'docs' / 'GUIDE.md'
        guide.write_bytes(guide.read_bytes() + b'\nNew actual note.\n')
        self.assertIn('New actual note.', context(self.projects, 'p', 'docs')['target']['document']['content'])

    def test_context_reaches_explicit_target_after_351_siblings_without_ui_scan(self):
        for number in range(351):
            (self.root / f'child-{number:03d}').mkdir()
        target = self.root / 'child-350'
        (target / 'README.md').write_text('# The explicit target\n', encoding='utf-8')
        self.assertFalse(any(node['directory'] == 'child-350'
                             for node in build_tree(self.projects)['nodes'].values()))
        with mock.patch('doctree.foldertree.build_tree', side_effect=AssertionError('UI traversal is forbidden')):
            result = context(self.projects, 'p', 'child-350')
        self.assertEqual(result['target']['directory'], 'child-350')
        self.assertIn('The explicit target', result['target']['document']['content'])
        self.assertEqual(result['usage']['directories_read'], 2)

    def test_context_reads_inside_runs_and_never_descends_into_grandchildren(self):
        current = self.root / 'runs' / 'current'
        (current / 'child' / 'grandchild').mkdir(parents=True)
        (current / 'README.md').write_text('# Current run\n', encoding='utf-8')
        (current / 'child' / 'README.md').write_text('# Child body not exported by default\n', encoding='utf-8')
        original = Path.iterdir
        visited = []

        def immediate_only(path):
            visited.append(path)
            if path == current / 'child' / 'grandchild':
                raise AssertionError('Grandchildren must not be scanned')
            return original(path)

        with mock.patch.object(Path, 'iterdir', immediate_only), \
                mock.patch('doctree.foldertree.build_tree', side_effect=AssertionError('No UI scan')):
            result = context(self.projects, 'p', 'runs/current')
        self.assertEqual(result['target']['directory'], 'runs/current')
        self.assertEqual([item['directory'] for item in result['children']], ['runs/current/child'])
        self.assertNotIn('document', result['children'][0])
        self.assertEqual(set(visited), {self.root, self.root / 'runs', current, current / 'child'})

    def test_context_body_and_total_json_budgets_report_truncation(self):
        text = ('中文 "quote" \\ newline\n' * 1000)
        guide = self.root / 'docs' / 'GUIDE.md'
        guide.write_text(guide.read_text(encoding='utf-8') + text, encoding='utf-8')
        result = context(self.projects, 'p', 'docs', max_body_chars=120, max_chars=4096)
        body = result['target']['document']
        self.assertLessEqual(body['content_chars'], 120)
        self.assertTrue(body['content_truncated'])
        self.assertFalse(body['source_truncated'])
        self.assertEqual(body['source_chars'], len(guide.read_bytes().decode('utf-8')))
        large = context(self.projects, 'p', 'docs', body_scope='all', max_body_chars=8000, max_chars=4096)
        serialized = json.dumps(large, ensure_ascii=False, indent=2) + '\n'
        self.assertLessEqual(len(serialized), 4096)
        self.assertEqual(large['usage']['output_chars'], len(serialized))
        self.assertTrue(large['truncation']['output'])
        self.assertTrue(large['target']['document']['content_truncated'])

    def test_context_child_and_ancestor_limits_are_separate_from_ui_depth(self):
        deepest = self.root / 'a' / 'b' / 'c' / 'd'
        deepest.mkdir(parents=True)
        for number in range(5):
            (deepest / f'child-{number}').mkdir()
        result = context(self.projects, 'p', 'a/b/c/d', max_ancestors=2, max_children=2)
        self.assertEqual([item['directory'] for item in result['ancestors']], ['.', 'a/b/c'])
        self.assertEqual(result['truncation']['ancestors_omitted'], 2)
        self.assertEqual(result['usage']['children_discovered'], 5)
        self.assertEqual(result['usage']['children_returned'], 2)
        self.assertEqual(result['truncation']['children_omitted'], 3)
        self.assertTrue(result['usage']['child_count_complete'])
        self.assertEqual(len(result['children']), 2)

    def test_context_can_omit_all_bodies_without_modifying_source_files(self):
        before = {path.relative_to(self.root).as_posix(): path.read_bytes()
                  for path in self.root.rglob('*.md')}
        result = context(self.projects, 'p', '.', body_scope='none', max_children=0, max_ancestors=0)
        self.assertNotIn('document', result['target'])
        self.assertEqual(result['children'], [])
        self.assertEqual(result['ancestors'], [])
        self.assertEqual(before, {path.relative_to(self.root).as_posix(): path.read_bytes()
                                  for path in self.root.rglob('*.md')})

    def test_context_read_and_entry_budgets_do_not_make_partial_reads_look_complete(self):
        partial = context(self.projects, 'p', '.', max_markdown_bytes=20)
        self.assertLessEqual(partial['usage']['markdown_bytes_read'], 20)
        self.assertTrue(partial['truncation']['source_read'])
        self.assertTrue(partial['target']['document']['source_truncated'])
        self.assertIsNone(partial['target']['document']['sha256'])
        self.assertIsNone(partial['target']['version'])
        entries = context(self.projects, 'p', '.', max_entries_per_directory=1)
        self.assertFalse(entries['usage']['child_count_complete'])
        self.assertTrue(entries['truncation']['directory_entries'])
        self.assertEqual(entries['target']['entry'], 'README.md')

    def test_context_rejects_hidden_linked_and_outside_target_paths(self):
        for directory in ('..', '.doctree', 'C:/outside', 'docs/../records'):
            with self.subTest(directory=directory), self.assertRaises(ValueError):
                context(self.projects, 'p', directory)
        alias = self.root / 'linked'
        try:
            alias.symlink_to(self.root / 'docs', target_is_directory=True)
        except OSError as exc:
            self.skipTest(f'Platform cannot create symlinks: {exc}')
        self.addCleanup(alias.unlink)
        with self.assertRaises(ValueError):
            context(self.projects, 'p', 'linked')

    def test_context_options_reject_invalid_values(self):
        for options in ({'body_scope': 'children'}, {'max_chars': 2000}, {'max_chars': True},
                        {'max_body_chars': -1}, {'max_children': -1}, {'max_ancestors': -1},
                        {'max_entries_per_directory': 0}, {'max_markdown_bytes': 0}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                context(self.projects, 'p', '.', **options)

    def test_source_path_is_guarded_and_does_not_execute_markdown(self):
        target = self.root / 'docs' / 'payload.md'
        target.write_text('<script>bad()</script>', encoding='utf-8')
        self.assertIn('<script>', read_source(self.projects, 'p', 'docs/payload.md')['content'])
        for path in ('../README.md', '.git/config.md', '.doctree/README.md', 'C:/README.md'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                read_source(self.projects, 'p', path)

    def test_open_folder_checks_scope_before_native_action(self):
        with mock.patch('doctree.foldertree.sys.platform', 'win32'), mock.patch('doctree.foldertree.os.startfile', create=True) as opener:
            result = open_folder(self.projects, 'p', 'docs')
            self.assertTrue(result['opened'])
            opener.assert_called_once_with(str(self.root / 'docs'))
            with self.assertRaises(ValueError):
                open_folder(self.projects, 'p', '..')
            self.assertEqual(opener.call_count, 1)

    def test_windows_reparse_attributes_detect_junction_without_new_path_api(self):
        path = mock.Mock(spec=['lstat'])
        path.lstat.return_value = SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400)
        self.assertTrue(_link(path))
        path.lstat.return_value = SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0)
        self.assertFalse(_link(path))

    def test_project_root_rejects_link_in_ancestor_path(self):
        alias = self.root / 'alias'
        try:
            alias.symlink_to(self.root / 'docs', target_is_directory=True)
        except OSError as exc:
            self.skipTest(f'Platform cannot create symlinks: {exc}')
        self.addCleanup(alias.unlink)
        (self.root / 'docs' / 'nested').mkdir()
        with self.assertRaisesRegex(ValueError, '普通本地目录'):
            project_path(alias / 'nested', '.', directory=True)

    def test_hidden_markdown_cannot_appear_as_a_managed_node_or_body(self):
        hidden = self.root / 'records' / '.private.md'
        hidden.write_bytes((self.root / 'docs' / 'GUIDE.md').read_bytes().replace(b'p.docs', b'p.secret'))
        tree = build_tree(self.projects)
        self.assertNotIn('p.secret', tree['nodes'])
        record = next(node for node in tree['nodes'].values() if node['directory'] == 'records')
        self.assertFalse(record['managed'])
        self.assertIsNone(record['entry'])
        with self.assertRaisesRegex(ValueError, '隐藏'):
            read_source(self.projects, 'p', 'records/.private.md')

    def test_node_depth_and_discovery_budgets_are_explicit(self):
        tree = build_tree(self.projects, max_nodes=1)
        self.assertEqual(len(tree['nodes']), 1)
        self.assertTrue(tree['warnings'])
        self.assertIn('数量达到上限', tree['nodes']['p.root']['collapsed_reason'])
        shallow = build_tree(self.projects, max_depth=0)
        self.assertEqual(len(shallow['nodes']), 1)
        self.assertIn('深度', shallow['nodes']['p.root']['collapsed_reason'])
        partial = build_tree(self.projects, max_entries_per_directory=1)
        self.assertTrue(partial['warnings'])
        self.assertTrue(partial['nodes']['p.root']['counts_partial'])
        self.assertTrue(partial['nodes']['p.root']['metadata_incomplete'])
        self.assertEqual(partial['nodes']['p.root']['entry'], 'README.md')
        limited_text = build_tree(self.projects, max_markdown_bytes=1)
        self.assertTrue(limited_text['warnings'])
        self.assertTrue(any(node['metadata_incomplete'] for node in limited_text['nodes'].values()))
        for kwargs in ({'max_nodes': 0}, {'max_depth': -1}, {'max_entries_per_directory': 0},
                       {'max_markdown_bytes': 0}, {'max_nodes': True}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                build_tree(self.projects, **kwargs)

    def test_duplicate_project_and_node_ids_fail_instead_of_ambiguous_navigation(self):
        with self.assertRaisesRegex(ValueError, '项目 ID 重复'):
            build_tree(self.projects + self.projects)
        duplicate = self.root / 'records' / 'README.md'
        duplicate.write_bytes((self.root / 'docs' / 'GUIDE.md').read_bytes())
        with self.assertRaisesRegex(ValueError, '节点 ID 重复'):
            build_tree(self.projects)

    def test_history_and_artifact_links_are_readable_while_default_tree_stays_collapsed(self):
        directories = ('history', 'build', 'dist', 'outputs', 'overleaf_upload_2026')
        selections = []
        for directory in directories:
            folder = self.root / directory
            (folder / 'batch-01').mkdir(parents=True)
            selections.extend([{'directory': directory, 'initial_body': '# Artifact navigation\n'},
                               {'directory': directory + '/batch-01', 'initial_body': '# Batch source\n'}])
        apply_plan(plan_sync(self.root, selections, 'p'))
        tree = build_tree(self.projects)
        for directory in directories:
            with self.subTest(directory=directory):
                node = next(node for node in tree['nodes'].values() if node['directory'] == directory)
                self.assertTrue(node['managed'])
                self.assertTrue(node['has_readme'])
                self.assertFalse(node['readme_required'])
                self.assertTrue(node['collapsed_reason'])
                self.assertEqual(node['children'], [])
                self.assertEqual(node['child_count'], 1)
                self.assertNotIn(directory, tree['scan_policy']['hidden_directories'])
                entry = read_source(self.projects, 'p', directory + '/README.md')
                self.assertIn('(batch-01/README.md)', entry['content'])
                child = read_source(self.projects, 'p', directory + '/batch-01/README.md')
                self.assertIn('# Batch source', child['content'])
        for directory in ('node_modules', 'vendor', 'deps', '.doctree'):
            with self.subTest(technical_directory=directory), self.assertRaises(ValueError):
                project_path(self.root, directory, directory=True)
