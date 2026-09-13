"""Review-loop regression fixtures use only small, independent temporary repos."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from doctree import maintenance, markdown_protocol as protocol


@unittest.skipUnless(shutil.which('git'), 'Git is required for maintenance checks')
class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'project'
        self.root.mkdir()
        self.git('init', '-q')
        self.git('config', 'user.name', 'DocTree test fixture')
        self.git('config', 'user.email', 'fixture@example.invalid')
        self.git('config', 'core.autocrlf', 'false')
        self.git('config', 'core.hooksPath', str(self.root / '.git/fixture-no-hooks'))
        self.git('config', 'commit.gpgsign', 'false')
        self.write('.gitignore', '.doctree/\nprivate/\n')
        self.write('README.md', '# Root guide\n\nRoot scope.\n')
        self.write('src/GUIDE.md', '# Source guide\n\nSource scope.\n')
        self.write('src/leaf/README.md', '# Leaf guide\n\nLeaf scope.\n')
        self.write('src/leaf/input.txt', 'baseline input\n')
        self.write('src/input.txt', 'baseline parent input\n')
        protocol.apply_plan(protocol.plan_sync(self.root, [
            {'directory': '.', 'id': 'p', 'title': 'Root', 'purpose': 'Maintain the project entry'},
            {'directory': 'src', 'entry': 'GUIDE.md', 'id': 'p.src', 'title': 'Source', 'purpose': 'Maintain source guidance'},
            {'directory': 'src/leaf', 'id': 'p.leaf', 'title': 'Leaf', 'purpose': 'Maintain leaf guidance'},
        ], 'p'))
        self.commit('Initial fixture')
        maintenance.initialize(self.root)

    def git(self, *args):
        result = subprocess.run(['git', '-C', str(self.root), *args],
                                capture_output=True, text=True, encoding='utf-8', check=True)
        return result.stdout.strip()

    def write(self, relative, text):
        file = self.root / relative
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(text, encoding='utf-8', newline='')
        return file

    def commit(self, message):
        self.git('add', '--all')
        self.git('commit', '-q', '-m', message)

    def nodes(self):
        report = maintenance.check(self.root)
        self.assertTrue(report['complete'])
        return {row['node_id']: row for row in report['nodes']}

    def acknowledge(self, node_id, *, token=None, parent_impact='not_needed'):
        return maintenance.acknowledge(
            self.root, node_id=node_id, token=token or self.nodes()[node_id]['token'],
            decision='no_change', reason='Read the changed input; current guidance remains accurate.',
            reviewer='fixture-agent', parent_impact=parent_impact,
            parent_reason='The stated scope determines whether the direct parent needs a check.')

    def change_navigation(self, relative):
        path = self.root / relative
        raw = path.read_bytes()
        self.assertIn(protocol.NAV_END.encode(), raw)
        path.write_bytes(raw.replace(protocol.NAV_END.encode(), b'> Navigation-only note.\n' + protocol.NAV_END.encode(), 1))

    def test_initialize_and_check_do_not_claim_prior_work_reviewed_or_write_sources(self):
        before = {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob('*.md')}
        record_before = (self.root / maintenance.RECORD).read_bytes()
        report = maintenance.check(self.root)
        self.assertTrue(report['complete'])
        self.assertEqual(report['pending'], 0)
        self.assertTrue(all(row['status'] == 'baseline' for row in report['nodes']))
        self.assertEqual(maintenance.initialize(self.root)['status'], 'already_initialized')
        self.assertEqual(record_before, (self.root / maintenance.RECORD).read_bytes())
        self.assertEqual(before, {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob('*.md')})
        self.assertEqual(self.nodes()['p.src']['entry'], 'src/GUIDE.md')

    def test_acknowledge_records_current_version_and_preserves_previous_record_backup(self):
        before = (self.root / maintenance.RECORD).read_bytes()
        self.write('src/leaf/input.txt', 'changed input\n')
        result = self.acknowledge('p.leaf')
        self.assertEqual(result['status'], 'recorded')
        self.assertEqual(self.nodes()['p.leaf']['status'], 'reviewed')
        backups = list((self.root / '.doctree/backups/maintenance').glob('*.json'))
        self.assertTrue(any(path.read_bytes() == before for path in backups))
        self.assertEqual(self.acknowledge('p.leaf')['status'], 'unchanged')

    def test_stale_token_rejects_without_changing_shared_record(self):
        self.write('src/leaf/input.txt', 'first input\n')
        token = self.nodes()['p.leaf']['token']
        before = (self.root / maintenance.RECORD).read_bytes()
        self.write('src/leaf/input.txt', 'second input\n')
        with self.assertRaisesRegex(ValueError, '版本|变化'):
            self.acknowledge('p.leaf', token=token)
        self.assertEqual(before, (self.root / maintenance.RECORD).read_bytes())

    def test_index_flags_that_hide_changes_fail_closed_without_clearing_flags(self):
        relative = 'src/leaf/input.txt'
        original = (self.root / relative).read_bytes()
        self.acknowledge('p.leaf')
        token = self.nodes()['p.leaf']['token']
        before = (self.root / maintenance.RECORD).read_bytes()
        for flag in ('assume-unchanged', 'skip-worktree'):
            with self.subTest(flag=flag):
                self.git('update-index', '--' + flag, '--', relative)
                expected_flags = self.git('ls-files', '-v', '--', relative)
                try:
                    self.write(relative, 'Changed input concealed by an index flag\n')
                    with self.assertRaisesRegex(ValueError, 'input.txt.*检查未完成'):
                        maintenance.check(self.root)
                    with self.assertRaisesRegex(ValueError, 'assume-unchanged / skip-worktree'):
                        self.acknowledge('p.leaf', token=token)
                    self.assertEqual(self.git('ls-files', '-v', '--', relative), expected_flags)
                    self.assertEqual(before, (self.root / maintenance.RECORD).read_bytes())
                finally:
                    self.git('update-index', '--no-' + flag, '--', relative)
                    (self.root / relative).write_bytes(original)

    def test_only_needed_child_decision_queues_direct_parent(self):
        self.write('src/leaf/input.txt', 'changed input\n')
        initial = self.nodes()
        self.assertEqual(initial['p.leaf']['status'], 'needs_review')
        self.assertEqual(initial['p.src']['status'], 'baseline')
        self.assertEqual(initial['p']['status'], 'baseline')
        self.acknowledge('p.leaf', parent_impact='needed')
        requested = self.nodes()
        self.assertEqual(requested['p.src']['status'], 'needs_review')
        self.assertEqual(requested['p.src']['child_requests'][0]['node_id'], 'p.leaf')
        self.assertEqual(requested['p']['status'], 'baseline')
        self.acknowledge('p.src', parent_impact='not_needed')
        completed = self.nodes()
        self.assertEqual(completed['p.src']['status'], 'reviewed')
        self.assertEqual(completed['p']['status'], 'baseline')
        # New leaf input cannot continue using its previous reviewed version.
        self.write('src/leaf/input.txt', 'another changed input\n')
        next_change = self.nodes()
        self.assertEqual(next_change['p.leaf']['status'], 'needs_review')
        self.assertEqual(next_change['p.src']['child_requests'], [])
        # An expired child request is not a new needed declaration. Its parent
        # waits for the child's next explicit impact decision.
        self.assertNotEqual(next_change['p.src']['status'], 'needs_review')
        self.assertEqual(next_change['p']['status'], 'baseline')
        self.acknowledge('p.leaf', parent_impact='not_needed')
        self.assertNotEqual(self.nodes()['p.src']['status'], 'needs_review')
        # Independent parent input still invalidates its review and can be
        # explicitly propagated one level further.
        self.write('src/input.txt', 'changed parent scope input\n')
        self.assertEqual(self.nodes()['p.src']['status'], 'needs_review')
        self.acknowledge('p.src', parent_impact='needed')
        self.assertEqual(self.nodes()['p']['status'], 'needs_review')

    def test_pure_navigation_changes_do_not_create_review_work(self):
        before = self.nodes()['p.leaf']['token']
        self.change_navigation('src/leaf/README.md')
        current = self.nodes()['p.leaf']
        self.assertEqual(current['status'], 'baseline')
        self.assertEqual(current['changes'], [])
        self.assertEqual(current['token'], before)

    def test_pure_navigation_changes_preserve_bom_document_semantics(self):
        path = self.root / 'src/leaf/README.md'
        path.write_bytes(b'\xef\xbb\xbf' + path.read_bytes())
        self.commit('BOM document fixture')
        state = json.loads((self.root / maintenance.RECORD).read_text(encoding='utf-8'))
        state['base_commit'] = self.git('rev-parse', 'HEAD')
        (self.root / maintenance.RECORD).write_text(json.dumps(state), encoding='utf-8')
        before = self.nodes()['p.leaf']['token']
        self.change_navigation('src/leaf/README.md')
        current = self.nodes()['p.leaf']
        self.assertEqual(current['status'], 'baseline')
        self.assertEqual(current['changes'], [])
        self.assertEqual(current['token'], before)

    def test_pure_navigation_changes_survive_git_line_ending_normalization(self):
        self.write('.gitattributes', '*.md text eol=crlf\n')
        path = self.root / 'src/leaf/README.md'
        path.write_bytes(path.read_bytes().replace(b'\n', b'\r\n'))
        self.commit('Line-ending fixture')
        state = json.loads((self.root / maintenance.RECORD).read_text(encoding='utf-8'))
        state['base_commit'] = self.git('rev-parse', 'HEAD')
        (self.root / maintenance.RECORD).write_text(json.dumps(state), encoding='utf-8')
        before = self.nodes()['p.leaf']['token']
        self.change_navigation('src/leaf/README.md')
        current = self.nodes()['p.leaf']
        self.assertEqual(current['status'], 'baseline')
        self.assertEqual(current['changes'], [])
        self.assertEqual(current['token'], before)

    def test_deleting_an_entire_directory_reports_deleted_paths(self):
        # Only paths under the test-owned TemporaryDirectory are removed.
        leaf = self.root / 'src/leaf'
        self.assertTrue(leaf.resolve().is_relative_to(Path(self.temp.name).resolve()))
        shutil.rmtree(leaf)
        nodes = self.nodes()
        self.assertNotIn('p.leaf', nodes)
        self.assertEqual(nodes['p.src']['status'], 'needs_review')
        changed = {row['path']: row['status'] for row in nodes['p.src']['changes']}
        self.assertEqual(changed['src/leaf/input.txt'], 'deleted')
        self.assertEqual(changed['src/leaf/README.md'], 'deleted')

    def test_renaming_a_managed_directory_preserves_identity_and_reports_old_paths(self):
        (self.root / 'src/leaf').rename(self.root / 'src/renamed')
        nodes = self.nodes()
        self.assertEqual(nodes['p.leaf']['directory'], 'src/renamed')
        self.assertEqual(nodes['p.leaf']['parent'], 'p.src')
        self.assertEqual(nodes['p.leaf']['status'], 'needs_review')
        self.assertTrue(any(row['path'] == 'src/leaf/README.md' and row['status'] == 'deleted'
                            for row in nodes['p.src']['changes']))

    def test_committed_and_untracked_changes_are_seen_but_ignored_files_are_not(self):
        self.write('src/leaf/input.txt', 'committed changed input\n')
        self.commit('Changed fixture input')
        self.write('src/leaf/new.txt', 'untracked changed input\n')
        self.write('private/local.txt', 'PRIVATE FIXTURE SENTINEL\n')
        self.write('private/README.md', '<!-- doctree: invalid ignored fixture -->\n')
        report = maintenance.check(self.root)
        serialized = json.dumps(report, ensure_ascii=False)
        self.assertNotIn('private/', serialized)
        self.assertNotIn('PRIVATE FIXTURE SENTINEL', serialized)
        self.assertNotIn('committed changed input', serialized)
        leaf = next(row for row in report['nodes'] if row['node_id'] == 'p.leaf')
        self.assertEqual({row['path'] for row in leaf['changes']}, {'src/leaf/input.txt', 'src/leaf/new.txt'})

    def test_orphan_changes_remain_explicit_and_budget_failure_does_not_write(self):
        (self.root / 'README.md').unlink()
        self.write('outside.txt', 'No managed ancestor\n')
        report = maintenance.check(self.root)
        self.assertIn('outside.txt', report['unowned_changes'])
        before = (self.root / maintenance.RECORD).read_bytes()
        with mock.patch.object(maintenance, 'MAX_FILES', 1):
            with self.assertRaisesRegex(ValueError, '预算'):
                maintenance.check(self.root)
        self.assertEqual(before, (self.root / maintenance.RECORD).read_bytes())

    def test_nested_directory_cannot_borrow_outer_git_history(self):
        with self.assertRaisesRegex(ValueError, '独立 Git|外层'):
            maintenance.initialize(self.root / 'src')

    def test_hardlinked_source_is_not_read_as_project_owned_input(self):
        external = Path(self.temp.name) / 'external.txt'
        external.write_text('External private fixture', encoding='utf-8')
        try:
            os.link(external, self.root / 'src/leaf/linked.txt')
        except OSError as exc:
            self.skipTest(f'This filesystem cannot create hardlinks: {exc}')
        with self.assertRaisesRegex(ValueError, '硬链接|普通文件'):
            maintenance.check(self.root)

    def test_incomplete_review_record_cannot_claim_a_current_review(self):
        self.write('src/leaf/input.txt', 'changed input\n')
        token = self.nodes()['p.leaf']['token']
        path = self.root / maintenance.RECORD
        state = json.loads(path.read_text(encoding='utf-8'))
        state['reviews']['p.leaf'] = {'token': token, 'parent_impact': 'not_needed'}
        path.write_text(json.dumps(state), encoding='utf-8')
        before = path.read_bytes()
        with self.assertRaisesRegex(ValueError, '记录|格式|完整'):
            maintenance.check(self.root)
        self.assertEqual(path.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
