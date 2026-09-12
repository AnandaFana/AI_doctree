"""The CLI and local web interface share the same coordinator."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .scanner import scan, write_json, load_config
from .governance import StateStore

WORKSPACE = Path(__file__).resolve().parents[1]


def default_config():
    """Keep private project maps local; fresh checkouts use the shared samples."""
    local = WORKSPACE / 'config' / 'projects.local.json'
    return local if local.is_file() else WORKSPACE / 'config' / 'projects.json'


class Application:
    def __init__(self, config=None, state_dir=None):
        self.config = Path(config if config is not None else default_config()).resolve()
        self.state_dir = Path(state_dir or WORKSPACE / '.doctree').resolve()
        self.store = StateStore(self.state_dir / 'state.json')

    def refresh(self):
        index = scan(self.config)
        state = self.store.refresh(index)
        write_json(self.state_dir / 'index.json', index)
        return state

    def state(self):
        state = self.store.get_state()
        return state if state.get('nodes') else self.refresh()

    def project_specs(self):
        config, workspace = load_config(self.config)
        return [{**project, 'root': str((workspace / project['root']).absolute())}
                for project in config['projects']]

    def tree(self):
        from .foldertree import build_tree
        result = build_tree(self.project_specs())
        result['viewer'] = {'detailed_governance': True}
        return result

    def candidates(self):
        self.refresh()
        results, skipped = [], []
        for node in self.store.pending()['nodes']:
            try:
                proposal = self.store.prepare_summary(node['id'])
                proposal.update(summary=proposal['suggested_summary'], review='candidate', reviewer='deterministic-template')
                results.append(self.store.commit_summary(proposal))
            except ValueError as exc:
                skipped.append({'node_id': node['id'], 'reason': str(exc)})
        return {'results': results, 'skipped': skipped, 'pending': self.store.pending(),
                'notice': '模板只产生待审阅候选；审阅后提交才能清除待汇总状态。', 'state': self.store.get_state()}


def output(value, destination=None):
    if destination:
        write_json(destination, value)
        print(f'已写入 {Path(destination).resolve()}')
    else:
        print(json.dumps(value, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description='DocTree 本地项目地图与可追溯交接')
    parser.add_argument('--config', type=Path)
    parser.add_argument('--state-dir', type=Path)
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('scan', 'state', 'pending', 'mark-viewed', 'rollup'):
        cmd = sub.add_parser(name)
        cmd.add_argument('--output', type=Path)
    for name in ('context', 'prepare-summary'):
        cmd = sub.add_parser(name)
        cmd.add_argument('node_id')
        cmd.add_argument('--output', type=Path)
    for name in ('import-delivery', 'commit-summary'):
        cmd = sub.add_parser(name)
        cmd.add_argument('file', type=Path)
    serve = sub.add_parser('serve')
    serve.add_argument('--port', type=int, default=8765)
    tree = sub.add_parser('tree', help='从项目 Markdown 重建物理目录树')
    tree.add_argument('--output', type=Path)
    install = sub.add_parser('install', help='安装可单独运行的 .doctree 管理目录')
    install.add_argument('root', type=Path)
    install.add_argument('--project-id')
    install.add_argument('--title')
    sync = sub.add_parser('sync', help='生成或更新 Markdown 导航；默认只预览')
    sync.add_argument('root', type=Path)
    sync.add_argument('--selections', type=Path, help='JSON 数组：directory、entry、id、title、purpose')
    sync.add_argument('--project-id')
    sync.add_argument('--apply', action='store_true')
    sync.add_argument('--output', type=Path)
    args = parser.parse_args()
    app = Application(args.config, args.state_dir)
    try:
        command = args.command
        if command in ('install', 'sync', 'tree'):
            if command == 'install':
                from .portable import install
                result = install(args.root, project_id=args.project_id, title=args.title)
            elif command == 'sync':
                from .markdown_protocol import plan_sync, apply_plan
                selections = json.loads(args.selections.read_text(encoding='utf-8-sig')) if args.selections else []
                result = plan_sync(args.root, selections, project_id=args.project_id or args.root.resolve().name)
                if args.apply:
                    result = apply_plan(result)
            else:
                result = app.tree()
            output(result, getattr(args, 'output', None))
            return
        if command == 'serve':
            from .server import serve
            serve(app, args.port)
            return
        if command == 'scan':
            state = app.refresh()
            result = {'scan_id': state['scan_id'], 'nodes': len(state['nodes']),
                      'projects': len(state['projects']), 'pending': app.store.pending()['count'],
                      'warnings': state.get('warnings', [])}
        elif command == 'state':
            result = app.state()
        elif command == 'pending':
            result = app.store.pending()
        elif command == 'mark-viewed':
            result = app.store.mark_viewed()
        elif command == 'context':
            app.refresh()
            result = app.store.export_context(args.node_id)
        elif command == 'prepare-summary':
            app.refresh()
            result = app.store.prepare_summary(args.node_id)
        elif command in ('import-delivery', 'commit-summary'):
            document = json.loads(args.file.read_text(encoding='utf-8-sig'))
            app.refresh()
            result = (app.store.import_delivery(document) if command == 'import-delivery'
                      else app.store.commit_summary(document))
        else:
            result = app.candidates()
        output(result, getattr(args, 'output', None))
    except (ValueError, OSError, KeyError) as exc:
        print(f'DocTree: {exc}', file=sys.stderr)
        sys.exit(2)


if __name__ == '__main__':
    main()
