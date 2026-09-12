from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from doctree.cli import Application, WORKSPACE


class ProjectConfigTests(unittest.TestCase):
    def test_fresh_checkout_uses_available_teaching_projects(self):
        config = WORKSPACE / 'config' / 'projects.json'
        projects = json.loads(config.read_text(encoding='utf-8'))['projects']
        self.assertEqual({p['source_type'] for p in projects}, {'sample'})
        self.assertTrue(all((WORKSPACE / p['root']).is_dir() for p in projects))
        with tempfile.TemporaryDirectory() as temp:
            app = Application(config, Path(temp))
            state = app.refresh()
            self.assertEqual(len(state['projects']), 2)
            self.assertTrue(state['nodes'])

    def test_local_config_precedence_and_explicit_override(self):
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            config_dir = workspace / 'config'
            config_dir.mkdir()
            shared = config_dir / 'projects.json'
            local = config_dir / 'projects.local.json'
            shared.write_text('{}', encoding='utf-8')
            with patch('doctree.cli.WORKSPACE', workspace):
                self.assertEqual(Application().config, shared)
                local.write_text('{}', encoding='utf-8')
                self.assertEqual(Application().config, local)
                self.assertEqual(Application(shared).config, shared)

    def test_invalid_local_config_is_reported_without_silent_fallback(self):
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            (workspace / 'config').mkdir()
            (workspace / 'config/projects.local.json').write_text('{', encoding='utf-8')
            with patch('doctree.cli.WORKSPACE', workspace):
                with self.assertRaises(json.JSONDecodeError):
                    Application().refresh()


if __name__ == '__main__':
    unittest.main()
