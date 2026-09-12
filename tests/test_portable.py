from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from doctree import markdown_protocol as protocol
from doctree import portable


class PortableInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.area = Path(self.temp.name)
        self.root = self.area / "original"
        self.root.mkdir()
        (self.root / "README.md").write_text("# Human project\n\nKeep this paragraph.\n", encoding="utf-8")
        (self.root / "docs").mkdir()
        plan = protocol.plan_sync(self.root, [
            {"directory": ".", "title": "Shared project", "purpose": "Provide the project entry"},
            {"directory": "docs", "title": "Documents", "purpose": "Explain the project"},
        ], project_id="shared")
        protocol.apply_plan(plan)

    def test_install_preserves_user_bodies_history_and_is_idempotent(self):
        (self.root / ".gitignore").write_bytes(b"\xef\xbb\xbf# Existing ignore\r\n*.secret\r\n")
        (self.root / "AGENTS.md").write_bytes(b"# User rules\r\n\r\nPreserve custom instructions.\r\n")
        state = self.root / ".doctree" / "state.json"
        state.write_bytes(b'{"events":["keep-history"]}')
        readme_before = (self.root / "README.md").read_bytes()
        before = {name: (self.root / name).read_bytes() for name in (".gitignore", "AGENTS.md")}
        report = portable.install(self.root, "local-hint", "Local hint")
        self.assertEqual(report["project"]["id"], "shared")
        self.assertEqual(report["project"]["title"], "Shared project")
        self.assertTrue(report["changed"])
        self.assertEqual(state.read_bytes(), b'{"events":["keep-history"]}')
        self.assertEqual((self.root / "README.md").read_bytes(), readme_before)
        for name in before:
            self.assertTrue((self.root / name).read_bytes().startswith(before[name]))
            candidates = [self.root / path for path in report["backups"] if path.endswith("/" + name)]
            self.assertEqual(len(candidates), 1)
            self.assertEqual(candidates[0].read_bytes(), before[name])
        self.assertIn("/.doctree/", (self.root / ".gitignore").read_text(encoding="utf-8-sig"))
        self.assertIn("[README.md](README.md)", (self.root / "AGENTS.md").read_text(encoding="utf-8"))
        again = portable.install(self.root)
        self.assertEqual(again["changed"], [])
        self.assertEqual(again["backups"], [])

    def test_relocation_and_independent_stdlib_runtime(self):
        portable.install(self.root)
        moved = self.area / "moved project"
        self.root.rename(moved)
        script = moved / ".doctree" / "manage.py"
        result = subprocess.run([sys.executable, "-I", "-S", "-X", "utf8", str(script), "tree"],
                                cwd=self.area, capture_output=True, text=True, encoding="utf-8", timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        tree = json.loads(result.stdout)
        self.assertEqual(tree["projects"][0]["id"], "shared")
        self.assertEqual(Path(tree["projects"][0]["root"]), moved)
        self.assertTrue(any(node["directory"] == "docs" and node["managed"] for node in tree["nodes"].values()))
        context = subprocess.run([sys.executable, "-I", "-S", "-X", "utf8", str(script), "context", "--directory", "docs"],
                                 cwd=self.area, capture_output=True, text=True, encoding="utf-8", timeout=20)
        self.assertEqual(context.returncode, 0, context.stderr)
        exported = json.loads(context.stdout)
        self.assertEqual(exported["target"]["directory"], "docs")
        self.assertEqual(exported["ancestors"][0]["directory"], ".")
        self.assertIn("Keep this paragraph.", exported["ancestors"][0]["document"]["content"])
        reinstall = subprocess.run([sys.executable, "-I", "-S", "-X", "utf8", str(script), "install"],
                                   cwd=self.area, capture_output=True, text=True, encoding="utf-8", timeout=20)
        self.assertEqual(reinstall.returncode, 0, reinstall.stderr)
        self.assertEqual(json.loads(reinstall.stdout)["changed"], [])
        lib = moved / ".doctree" / "lib"
        code = ("import sys; sys.path.insert(0, " + repr(str(lib)) + "); "
                "import doctree.portable, doctree.foldertree, doctree.markdown_protocol; "
                "assert not {'yaml', 'doctree.scanner', 'doctree.governance'}.intersection(sys.modules)")
        independent = subprocess.run([sys.executable, "-I", "-S", "-c", code], cwd=self.area,
                                     capture_output=True, text=True, timeout=20)
        self.assertEqual(independent.returncode, 0, independent.stderr)

    def test_sync_check_is_read_only_and_sync_existing_nodes_only(self):
        portable.install(self.root)
        (self.root / "new-unmanaged").mkdir()
        # A moved managed directory keeps its ID but requires parent-link repair.
        (self.root / "docs").rename(self.root / "manuals")
        doc = self.root / "manuals" / "README.md"
        doc.write_text(doc.read_text(encoding="utf-8") + "\nA new local finding.\n", encoding="utf-8")
        script = self.root / ".doctree" / "manage.py"
        before = {p: p.read_bytes() for p in self.root.rglob("*.md")}
        def run(*args):
            return subprocess.run([sys.executable, "-I", "-S", "-X", "utf8", str(script), *args],
                                  cwd=self.area, capture_output=True, text=True, encoding="utf-8", timeout=20)
        check = run("sync", "--check")
        self.assertEqual(check.returncode, 1, check.stderr)
        self.assertTrue(json.loads(check.stdout)["changes"])
        self.assertEqual({p: p.read_bytes() for p in before}, before)
        sync = run("sync")
        self.assertEqual(sync.returncode, 0, sync.stderr)
        self.assertFalse((self.root / "new-unmanaged" / "README.md").exists())
        self.assertEqual(run("sync", "--check").returncode, 0)

    def test_first_install_then_annotate_unicode_project_without_site_packages(self):
        fresh = self.area / "_中文目录"
        fresh.mkdir()
        (fresh / "README.md").write_text("# 初始项目\n\n保留原文。\n", encoding="utf-8")
        portable.install(fresh)
        script = fresh / ".doctree" / "manage.py"
        result = subprocess.run([sys.executable, "-I", "-S", "-X", "utf8", str(script),
                                 "annotate", "--directory", ".", "--purpose", "说明项目范围"],
                                cwd=self.area, capture_output=True, text=True, encoding="utf-8", timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "applied")
        text = (fresh / "README.md").read_text(encoding="utf-8")
        metadata = protocol.parse_document(text)
        self.assertRegex(metadata["id"], r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
        self.assertEqual(metadata["purpose"], "说明项目范围")
        self.assertIn("保留原文。", text)

    def test_malformed_shared_block_stops_before_any_install_write(self):
        (self.root / "AGENTS.md").write_text("User rules\n" + portable.AGENTS_START, encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "标记"):
            portable.install(self.root)
        self.assertFalse((self.root / ".doctree" / "manage.py").exists())
        self.assertFalse((self.root / ".gitignore").exists())

    def test_hardlink_and_management_symlink_are_refused(self):
        source = self.area / "shared-agent.md"
        source.write_text("Shared external instructions", encoding="utf-8")
        try:
            os.link(source, self.root / "AGENTS.md")
        except OSError as exc:
            self.skipTest(f"Hardlinks unavailable: {exc}")
        with self.assertRaisesRegex(ValueError, "硬链接"):
            portable.install(self.root)
        self.assertEqual(source.read_text(encoding="utf-8"), "Shared external instructions")
        (self.root / "AGENTS.md").unlink()
        management = self.root / ".doctree"
        # Protocol created backup history already; test the generated file link.
        try:
            (management / "manage.py").symlink_to(source)
        except OSError:
            return  # Windows may prohibit unprivileged symlinks; hardlink check ran.
        with self.assertRaisesRegex(ValueError, "链接"):
            portable.install(self.root)
        self.assertFalse((self.root / ".gitignore").exists())


class PortableServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        (cls.root / "README.md").write_text("# Source\n<script>alert('text')</script>\n", encoding="utf-8")
        cls.projects = [{"id": "p", "title": "Project", "root": str(cls.root)}]
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), portable.handler_for(cls.projects, 0))
        cls.port = cls.server.server_address[1]
        cls.server.RequestHandlerClass = portable.handler_for(cls.projects, cls.port)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.thread.join(3)
        cls.server.server_close()
        cls.temp.cleanup()

    def request(self, method, path, headers=None, body=None):
        connection = HTTPConnection("127.0.0.1", self.port, timeout=5)
        self.addCleanup(connection.close)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()

    def test_tree_readme_assets_and_fallback_details(self):
        for path in ("/", "/details", "/tree.css", "/tree.js", "/api/tree", "/api/health"):
            with self.subTest(path=path):
                self.assertEqual(self.request("GET", path)[0], 200)
        status, headers, body = self.request("GET", "/api/tree/source?project=p&path=README.md")
        self.assertEqual(status, 200)
        self.assertIn("application/json", headers["Content-Type"])
        self.assertIn("<script>", json.loads(body)["content"])
        self.assertIn("script-src 'self'", headers["Content-Security-Policy"])
        for path in ("../README.md", ".git/config", ".doctree/manage.py"):
            self.assertEqual(self.request("GET", "/api/tree/source?project=p&path=" + path)[0], 400)

    def test_cross_site_and_non_json_open_requests_rejected(self):
        self.assertEqual(self.request("GET", "/api/tree", {"Host": "attacker.example"})[0], 403)
        for origin in ("https://attacker.example", "null"):
            self.assertEqual(self.request("POST", "/api/tree/open-folder", {
                "Origin": origin, "Content-Type": "application/json"}, '{}')[0], 403)
        self.assertEqual(self.request("POST", "/api/tree/open-folder", {
            "Content-Type": "text/plain"}, '{}')[0], 415)
        self.assertEqual(self.request("POST", "/api/tree/open-folder", {
            "Content-Type": "application/json"}, '[]')[0], 400)
        for invalid in ('null', '{"project_id":[],"directory":"."}',
                        '{"project_id":"p","directory":null}', '{"project_id":"p","directory":""}'):
            self.assertEqual(self.request("POST", "/api/tree/open-folder", {
                "Content-Type": "application/json"}, invalid)[0], 400)
        with patch("doctree.foldertree.open_folder", return_value={"opened": True}) as opening:
            response = self.request("POST", "/api/tree/open-folder", {
                "Content-Type": "application/json", "Origin": f"http://127.0.0.1:{self.port}"},
                '{"project_id":"p","directory":"."}')
            self.assertEqual(response[0], 200)
            opening.assert_called_once_with(self.projects, "p", ".")


if __name__ == "__main__":
    unittest.main()
