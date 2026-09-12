import json
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from doctree.scanner import scan, safe_path, metadata, file_hash
from doctree.markdown_protocol import plan_sync, apply_plan


class ScannerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'config').mkdir()
        (self.root / 'source').mkdir()
        self.config = self.root / 'config' / 'projects.json'
        self.spec = {'schema': 1, 'projects': [{'id': 'p', 'title': '项目', 'root': 'source',
                                              'source_type': 'sample', 'mode': 'protocol'}]}
        self.write_config()

    def write_config(self):
        self.config.write_text(json.dumps(self.spec), encoding='utf-8')

    def node(self, path, node_id, parent=None, **extra):
        p = self.root / 'source' / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text('# 文档\n\n```yaml\n' + json.dumps({'project_node': {
            'schema': 1, 'id': node_id, 'parent': parent, **extra}}) + '\n```\n\n正文\n', encoding='utf-8')
        return p

    def test_source_read_only_and_generated_region_is_not_business_change(self):
        p = self.node('README.md', 'p')
        before = file_hash(p)
        first = scan(self.config)
        self.assertEqual(before, file_hash(p))
        p.write_text(p.read_text(encoding='utf-8') + '\n<!-- doctree:generated:start -->\n新摘要\n```yaml\nproject_node: {schema: 1, id: generated.example}\n```\n<!-- doctree:generated:end -->', encoding='utf-8')
        second = scan(self.config)
        self.assertEqual(first['nodes']['p']['version'], second['nodes']['p']['version'])
        self.assertNotEqual(first['projects'][0]['fingerprint'], second['projects'][0]['fingerprint'])
        p.write_text(p.read_text(encoding='utf-8') + '\n新业务证据', encoding='utf-8')
        self.assertNotEqual(second['nodes']['p']['version'], scan(self.config)['nodes']['p']['version'])

    def test_duplicate_cycle_and_invalid_related_are_rejected(self):
        self.node('README.md', 'p', 'c')
        self.node('c/README.md', 'c', 'p')
        with self.assertRaisesRegex(ValueError, '环'):
            scan(self.config)
        self.node('c/README.md', 'p')
        with self.assertRaisesRegex(ValueError, '重复'):
            scan(self.config)
        self.node('README.md', 'p', related=[{'id': 'missing', 'relation': 'depends_on'}])
        self.node('c/README.md', 'c', 'p')
        with self.assertRaisesRegex(ValueError, '关联'):
            scan(self.config)

    def test_legacy_children_resolve_without_second_parent_source(self):
        self.node('README.md', 'p', children=[{'entry': 'c/README.md', 'purpose': 'child'}])
        self.node('c/README.md', 'c')
        result = scan(self.config)
        self.assertEqual(result['nodes']['c']['parent'], 'p')
        self.assertEqual(result['nodes']['p']['children'], ['c'])

    def test_evidence_change_invalidates_owner_and_excluded_bulk_is_not_crawled(self):
        self.node('README.md', 'p', evidence=[{'path': 'records/one.json', 'label': '选定证据'}])
        records = self.root / 'source' / 'records'
        records.mkdir()
        (records / 'one.json').write_text('{"result":1}', encoding='utf-8')
        (records / 'unrelated.md').write_text('excluded', encoding='utf-8')
        first = scan(self.config)
        self.assertNotIn('records/unrelated.md', [f['path'] for f in first['projects'][0]['files']])
        (records / 'one.json').write_text('{"result":2}', encoding='utf-8')
        self.assertNotEqual(first['nodes']['p']['version'], scan(self.config)['nodes']['p']['version'])

    def test_traversal_and_git_internal_paths_rejected(self):
        for path in ('../secret.txt', '.git/config', 'C:/secret.txt', 'child/../../secret.txt'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                safe_path(self.root / 'source', path)

    def test_protocol_is_data_and_unsupported_schema_fails(self):
        with self.assertRaisesRegex(ValueError, 'schema'):
            metadata('```yaml\nproject_node: {schema: 99, id: p}\n```', 'README.md')
        with self.assertRaises(ValueError):
            metadata('```yaml\nproject_node: !!python/object/apply:os.system [echo bad]\n```', 'README.md')

    def test_unannotated_repository_generates_labeled_candidates(self):
        (self.root / 'source' / 'README.md').write_text('# Plain repo\nUntyped.', encoding='utf-8')
        result = scan(self.config)
        self.assertEqual(result['nodes']['p']['review'], 'candidate')
        self.assertTrue(result['warnings'])

    def test_mapped_detail_only_projects_headers_inside_its_existing_scan_scope(self):
        source = self.root / 'source'
        (source / 'README.md').write_text('# Root\nExisting body.', encoding='utf-8')
        (source / 'work').mkdir()
        (source / 'history' / 'batch').mkdir(parents=True)
        apply_plan(plan_sync(source, [{'directory': '.', 'id': 'p'},
                                     {'directory': 'work', 'id': 'p.work'},
                                     {'directory': 'history', 'id': 'p.history'},
                                     {'directory': 'history/batch', 'id': 'p.batch'}], 'p'))
        self.spec['projects'][0].update(mode='mapped', nodes=[{
            'id': 'p', 'entry': 'README.md', 'parent': None, 'kind': 'project',
            'summary': 'Existing reviewed scope stays separate.',
            'stages': {'acceptance': 'pending'}}])
        self.write_config()
        with patch('doctree.markdown_protocol.discover_nodes', side_effect=AssertionError('detail must not rescan all directory nodes')):
            result = scan(self.config)
        self.assertEqual(set(result['nodes']), {'p', 'p.work'})
        self.assertEqual(result['nodes']['p']['summary'], 'Existing reviewed scope stays separate.')
        self.assertEqual(result['nodes']['p']['stages']['acceptance'], 'pending')
        self.assertFalse(any(file['path'].startswith('history/') for file in result['projects'][0]['files']))
        self.assertFalse(result['projects'][0]['scan_policy']['full_directory_coverage'])
        self.assertIn('no independent full-directory', result['projects'][0]['scan_policy']['markdown_metadata_scope'])


if __name__ == '__main__':
    unittest.main()
