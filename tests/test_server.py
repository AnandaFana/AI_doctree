from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

from doctree.cli import Application
from doctree.server import handler_for
from doctree import __version__


class ServerBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls.root = root
        (root / 'config').mkdir()
        (root / 'source').mkdir()
        (root / 'source' / 'README.md').write_text('# Test\n<script>alert(1)</script>', encoding='utf-8')
        (root / 'source' / 'private.secret').write_text('not indexed', encoding='utf-8')
        (root / 'source' / 'safe & local').mkdir()
        (root / 'source' / 'safe & local' / 'README.md').write_text('# Child\nA directory.', encoding='utf-8')
        (root / 'source' / '.private').mkdir()
        (root / 'source' / '.private' / 'note.md').write_text('hidden document', encoding='utf-8')
        (root / 'outside.md').write_text('outside project boundary', encoding='utf-8')
        (root / 'config' / 'projects.json').write_text(json.dumps({'schema': 1, 'projects': [
            {'id': 'p', 'title': 'Test', 'root': 'source', 'source_type': 'sample'}]}), encoding='utf-8')
        cls.app = Application(root / 'config' / 'projects.json', root / 'state')
        cls.app.refresh()
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), handler_for(cls.app, 0))
        cls.port = cls.server.server_address[1]
        cls.server.RequestHandlerClass = handler_for(cls.app, cls.port)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.thread.join(3)
        cls.server.server_close()
        cls.tmp.cleanup()

    def request(self, method, path, headers=None, body=None):
        connection = HTTPConnection('127.0.0.1', self.port, timeout=5)
        self.addCleanup(connection.close)
        connection.request(method, path, body, headers or {})
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()

    def test_readme_source_is_json_and_out_of_manifest_file_is_denied(self):
        status, headers, body = self.request('GET', '/api/source?node=p&path=README.md')
        self.assertEqual(status, 200)
        self.assertIn('application/json', headers['Content-Type'])
        self.assertIn('<script>', json.loads(body)['content'])
        self.assertIn("script-src 'self'", headers['Content-Security-Policy'])
        for path in ('private.secret', '../README.md', '.git/config'):
            with self.subTest(path=path):
                self.assertEqual(self.request('GET', '/api/source?node=p&path=' + path)[0], 400)

    def test_dns_rebinding_and_cross_origin_write_rejected(self):
        self.assertEqual(self.request('GET', '/api/state', {'Host': 'attacker.example'})[0], 403)
        self.assertEqual(self.request('POST', '/api/viewed', {
            'Origin': 'https://attacker.example', 'Content-Type': 'application/json'}, '{}')[0], 403)
        self.assertEqual(self.request('POST', '/api/viewed', {'Content-Type': 'text/plain'}, '{}')[0], 415)
        self.assertEqual(self.request('POST', '/api/viewed', {'Content-Type': 'application/json'}, '{}')[0], 200)

    def test_tree_is_default_page_and_details_remains_available(self):
        for route in ('/', '/tree', '/index.html'):
            with self.subTest(route=route):
                status, headers, body = self.request('GET', route)
                self.assertEqual(status, 200)
                self.assertIn('text/html', headers['Content-Type'])
                self.assertIn(b'<script src="/tree.js"', body)
                self.assertIn(b'id="tree-canvas"', body)
                self.assertIn(b'href="/details"', body)
        status, headers, body = self.request('GET', '/details')
        self.assertEqual(status, 200)
        self.assertIn('text/html', headers['Content-Type'])
        self.assertIn(b'<script src="/app.js"', body)
        self.assertIn(b'href="/tree"', body)
        for route, content_type in (('/tree.js', 'text/javascript'), ('/tree.css', 'text/css')):
            with self.subTest(route=route):
                status, headers, body = self.request('GET', route)
                self.assertEqual(status, 200)
                self.assertIn(content_type, headers['Content-Type'])
                self.assertTrue(body)

    def test_health_reports_current_package_version(self):
        status, _, body = self.request('GET', '/api/health')
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)['version'], __version__)

    def test_tree_api_projects_physical_folders_without_governance_mutation(self):
        before = json.dumps(self.app.state(), sort_keys=True)
        status, headers, body = self.request('GET', '/api/tree')
        self.assertEqual(status, 200)
        self.assertIn('application/json', headers['Content-Type'])
        tree = json.loads(body)
        self.assertEqual(tree['schema'], 2)
        self.assertEqual(tree['protocol'], 'markdown-first-v2')
        project = next(project for project in tree['projects'] if project['id'] == 'p')
        root = tree['nodes'][project['root_id']]
        self.assertEqual(root['directory'], '.')
        self.assertEqual(root['entry'], 'README.md')
        self.assertFalse(root['managed'])
        self.assertTrue(root['readme_available'])
        directories = {tree['nodes'][child]['directory'] for child in root['children']}
        self.assertIn('safe & local', directories)
        self.assertNotIn('.private', directories)
        self.assertEqual(json.dumps(self.app.state(), sort_keys=True), before)
        self.assertEqual(self.request('GET', '/api/tree', {'Host': 'attacker.example'})[0], 403)

    def test_tree_source_keeps_script_as_json_text_and_validates_paths(self):
        status, headers, body = self.request('GET', '/api/tree/source?' + urlencode({'project': 'p', 'path': 'README.md'}))
        self.assertEqual(status, 200)
        self.assertIn('application/json', headers['Content-Type'])
        self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
        self.assertIn("script-src 'self'", headers['Content-Security-Policy'])
        source = json.loads(body)
        self.assertIn('<script>alert(1)</script>', source['content'])
        self.assertEqual(source['path'], 'README.md')
        self.assertEqual(len(source['sha256']), 64)
        status, _, body = self.request('GET', '/api/tree/source?' + urlencode({'project': 'p', 'path': 'safe & local/README.md'}))
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)['path'], 'safe & local/README.md')
        for path in ('../outside.md', '..\\outside.md', '.git/config.md', '.doctree/index.md',
                     '.private/note.md', 'private.secret', 'README.md:alternate', 'C:/outside.md'):
            with self.subTest(path=path):
                status, _, body = self.request('GET', '/api/tree/source?' + urlencode({'project': 'p', 'path': path}))
                self.assertEqual(status, 400)
                self.assertIn('error', json.loads(body))
                self.assertNotIn('outside project boundary', body.decode('utf-8'))
        for query in ('', 'project=p', 'project=missing&path=README.md'):
            with self.subTest(query=query):
                self.assertEqual(self.request('GET', '/api/tree/source?' + query)[0], 400)

    def test_tree_source_refuses_symbolic_link(self):
        link = self.root / 'source' / 'linked.md'
        try:
            link.symlink_to(self.root / 'outside.md')
        except OSError as exc:
            self.skipTest(f'Platform cannot create symlinks: {exc}')
        self.addCleanup(link.unlink)
        status, _, body = self.request('GET', '/api/tree/source?project=p&path=linked.md')
        self.assertEqual(status, 400)
        self.assertNotIn('outside project boundary', body.decode('utf-8'))

    def test_open_folder_validates_paths_types_and_origin_before_native_open(self):
        headers = {'Content-Type': 'application/json', 'Origin': f'http://127.0.0.1:{self.port}'}
        with patch('doctree.foldertree.sys.platform', 'win32'), patch('doctree.foldertree.os.startfile', create=True) as native:
            for directory in ('.', 'safe & local'):
                with self.subTest(directory=directory):
                    status, _, body = self.request('POST', '/api/tree/open-folder', headers,
                                                   json.dumps({'project_id': 'p', 'directory': directory}))
                    self.assertEqual(status, 200)
                    self.assertTrue(json.loads(body)['opened'])
                    native.assert_called_with(str(self.root / 'source' / directory))
            native.reset_mock()
            invalid = [{'project_id': 'missing', 'directory': '.'},
                       {'project_id': 'p', 'directory': '../'},
                       {'project_id': 'p', 'directory': '.private'},
                       {'project_id': 'p', 'directory': '.git'},
                       {'project_id': 'p', 'directory': 'README.md'},
                       {'project_id': 'p', 'directory': 'C:/Windows'},
                       {'project_id': 'p', 'directory': ['.']},
                       {'project_id': 'p', 'directory': None},
                       {'project_id': 'p', 'directory': 1},
                       {'project_id': 'p', 'directory': ''},
                       {'project_id': ['p'], 'directory': '.'},
                       {'project_id': 'p'}, [], None, 1, 'directory']
            for value in invalid:
                with self.subTest(value=value):
                    status, _, body = self.request('POST', '/api/tree/open-folder', headers, json.dumps(value))
                    self.assertEqual(status, 400)
                    self.assertIn('error', json.loads(body))
            status, _, _ = self.request('POST', '/api/tree/open-folder',
                                        {**headers, 'Origin': 'https://attacker.example'},
                                        json.dumps({'project_id': 'p', 'directory': '.'}))
            self.assertEqual(status, 403)
            status, _, _ = self.request('POST', '/api/tree/open-folder',
                                        {**headers, 'Content-Type': 'text/plain'},
                                        json.dumps({'project_id': 'p', 'directory': '.'}))
            self.assertEqual(status, 415)
            native.assert_not_called()


if __name__ == '__main__':
    unittest.main()
