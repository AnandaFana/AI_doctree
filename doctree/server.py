from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
from urllib.parse import urlsplit, parse_qs
import threading

from .scanner import safe_path, file_hash
from .foldertree import read_source as read_tree_source, open_folder
from . import __version__

WEB = Path(__file__).resolve().parents[1] / 'web'


def handler_for(app, port):
    class Handler(BaseHTTPRequestHandler):
        server_version = 'DocTree/0.1'

        def send(self, value, status=200, content_type='application/json; charset=utf-8'):
            content = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(content)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(content)

        def validate_host(self):
            allowed = {f'127.0.0.1:{port}', f'localhost:{port}'}
            if self.headers.get('Host') not in allowed:
                self.send({'error': '只允许本机来源'}, 403)
                return False
            return True

        def do_GET(self):
            if not self.validate_host():
                return
            url = urlsplit(self.path)
            params = parse_qs(url.query)
            try:
                if url.path == '/api/state':
                    self.send(app.state())
                elif url.path == '/api/tree':
                    self.send(app.tree())
                elif url.path == '/api/tree/source':
                    self.send(read_tree_source(app.project_specs(), params['project'][0], params['path'][0]))
                elif url.path == '/api/health':
                    self.send({'status': 'ok', 'app': 'DocTree', 'version': __version__})
                elif url.path == '/api/context':
                    app.refresh()
                    self.send(app.store.export_context(params['node'][0]))
                elif url.path == '/api/pending':
                    self.send(app.store.pending())
                elif url.path == '/api/source':
                    state = app.state()
                    node = state['nodes'][params['node'][0]]
                    relative = params['path'][0].replace('\\', '/')
                    project = next(p for p in state['projects'] if p['id'] == node['project_id'])
                    manifest = {f['path']: f for f in project['files']}
                    if relative not in manifest:
                        raise ValueError('该文件不在已扫描清单中，请在映射中明确引用后刷新')
                    target = safe_path(project['root'], relative)
                    if target.stat().st_size > 2 * 1024 * 1024:
                        raise ValueError('文件过大，界面只预览 2 MB 以内的文本')
                    if target.suffix.lower() not in {'.md', '.txt', '.rst', '.json', '.yaml', '.yml', '.toml', '.tex', '.py', '.csv'}:
                        raise ValueError('该证据为二进制文件，请根据本地路径在相应应用中打开')
                    actual = file_hash(target)
                    self.send({'path': relative, 'content': target.read_text(encoding='utf-8-sig', errors='replace'),
                               'sha256': actual, 'scanned_sha256': manifest[relative]['sha256'],
                               'stale': actual != manifest[relative]['sha256']})
                elif url.path in ('/', '/index.html', '/tree', '/tree.html', '/tree.js', '/tree.css', '/details', '/app.js', '/styles.css'):
                    name = ('tree.html' if url.path in ('/', '/index.html', '/tree') else
                            'index.html' if url.path == '/details' else url.path[1:])
                    target = WEB / name
                    mime = {'html': 'text/html', 'js': 'text/javascript', 'css': 'text/css'}[target.suffix[1:]]
                    self.send(target.read_bytes(), content_type=mime + '; charset=utf-8')
                else:
                    self.send({'error': '入口不存在'}, 404)
            except (ValueError, KeyError, OSError) as exc:
                self.send({'error': str(exc)}, 400)

        def do_POST(self):
            if not self.validate_host():
                return
            origin = self.headers.get('Origin')
            allowed = {f'http://127.0.0.1:{port}', f'http://localhost:{port}'}
            if origin and origin not in allowed:
                self.send({'error': '不允许跨站写入'}, 403)
                return
            if self.headers.get_content_type() != 'application/json':
                self.send({'error': '写入必须使用 application/json'}, 415)
                return
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 <= length <= 1024 * 1024:
                    raise ValueError('请求过大')
                data = json.loads(self.rfile.read(length) or b'{}')
                if not isinstance(data, dict):
                    raise ValueError('请求内容必须是 JSON 对象')
                if self.path == '/api/refresh':
                    self.send(app.refresh())
                elif self.path == '/api/tree/refresh':
                    self.send(app.tree())
                elif self.path == '/api/tree/open-folder':
                    if any(not isinstance(data.get(key), str) or not data[key].strip()
                           for key in ('project_id', 'directory')):
                        raise ValueError('project_id 和 directory 必须是非空字符串')
                    self.send(open_folder(app.project_specs(), data['project_id'], data['directory']))
                elif self.path == '/api/viewed':
                    self.send(app.store.mark_viewed())
                elif self.path == '/api/rollup':
                    self.send(app.candidates())
                elif self.path == '/api/delivery':
                    app.refresh()
                    self.send(app.store.import_delivery(data))
                elif self.path == '/api/summary/prepare':
                    app.refresh()
                    self.send(app.store.prepare_summary(data['node_id']))
                elif self.path == '/api/summary/commit':
                    app.refresh()
                    self.send(app.store.commit_summary(data))
                elif self.path == '/api/stop':
                    self.send({'status': 'stopping'})
                    threading.Thread(target=self.server.shutdown, daemon=True).start()
                else:
                    self.send({'error': '入口不存在'}, 404)
            except (ValueError, KeyError, OSError) as exc:
                self.send({'error': str(exc)}, 409 if type(exc).__name__ == 'ConflictError' else 400)

        def log_message(self, format, *args):
            # Do not log user document bodies or query strings.
            pass

    return Handler


def serve(app, port=8765):
    app.refresh()
    server = ThreadingHTTPServer(('127.0.0.1', port), handler_for(app, port))
    print(f'DocTree 已启动：http://127.0.0.1:{port} （Ctrl+C 停止）', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
