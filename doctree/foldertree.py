"""A disposable directory map projected from on-disk Markdown.

This module has no dependency on the legacy catalog, governance store or PyYAML.
"""
from __future__ import annotations

import hashlib
from itertools import islice
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import subprocess
import stat
import sys
from datetime import datetime, timezone

from .markdown_protocol import DEFAULT_EXCLUDES, parse_document, semantic_text

# Technical internals are inaccessible. Large source/artifact folders remain
# readable and visible as entry nodes; only their default visual expansion stops.
HIDDEN = set(DEFAULT_EXCLUDES)
BULK = {'records', 'runs', 'output', 'outputs', 'history', 'build', 'dist'}
BULK_PREFIXES = ('overleaf_upload',)
MAX_TEXT = 1_000_000


def _link(path):
    # Path.is_junction is only available from Python 3.12. lstat attributes also
    # catch Windows junctions and other reparse points on supported Python 3.10.
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    return (stat.S_ISLNK(info.st_mode)
            or bool(getattr(info, 'st_file_attributes', 0)
                    & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400)))


def project_path(root, relative='.', *, directory=False):
    """Check each component; never traverse Git internals, links or device paths."""
    root = Path(root).absolute()
    if not isinstance(relative, (str, Path)) or not str(relative).strip():
        raise ValueError('项目相对路径必须是非空文本')
    value = str(relative).replace('\\', '/')
    rel = PurePosixPath(value)
    if rel.is_absolute() or PureWindowsPath(value).drive or '..' in rel.parts or ':' in value:
        raise ValueError('路径必须位于已接入的项目内')
    if any(part.casefold() in HIDDEN or part.startswith('.') and part not in ('.', '') for part in rel.parts):
        raise ValueError('不读取隐藏目录或管理内部文件')
    target = root
    if any(_link(ancestor) for ancestor in (*reversed(root.parents), root)) or not root.is_dir():
        raise ValueError('项目根必须是普通本地目录')
    for part in rel.parts:
        target = target / part
        if _link(target):
            raise ValueError('不跟随符号链接或目录联接')
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError('路径超出项目范围')
    if not (target.is_dir() if directory else target.is_file()):
        raise ValueError('目录或文件不存在')
    return target


def _project(projects, project_id):
    try:
        return next(p for p in projects if p['id'] == project_id)
    except StopIteration as exc:
        raise ValueError('项目未接入') from exc


def read_source(projects, project_id, path):
    project = _project(projects, project_id)
    target = project_path(project['root'], path)
    if target.suffix.lower() != '.md' or target.stat().st_size > MAX_TEXT:
        raise ValueError('轻量页面只预览 1 MB 以内的 Markdown')
    raw = target.read_bytes()
    return {'path': target.relative_to(Path(project['root']).absolute()).as_posix(),
            'content': raw.decode('utf-8-sig'), 'sha256': hashlib.sha256(raw).hexdigest()}


def open_folder(projects, project_id, directory):
    project = _project(projects, project_id)
    target = project_path(project['root'], directory, directory=True)
    # Shell metacharacters never become commands; only the native folder viewer runs.
    if sys.platform == 'win32':
        os.startfile(str(target))
    elif sys.platform == 'darwin':
        subprocess.Popen(['open', str(target)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        subprocess.Popen(['xdg-open', str(target)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return {'opened': True, 'path': str(target)}


def context(projects, project_id, directory='.'):
    project = _project(projects, project_id)
    project_path(project['root'], directory, directory=True)
    tree = build_tree([project], max_depth=12)
    relative = PurePosixPath(str(directory).replace('\\', '/')).as_posix()
    target = next((n for n in tree['nodes'].values() if n['directory'] == relative), None)
    if target is None:
        raise ValueError('该目录未在受限扫描中展开；请使用更上层的 README 导航')
    def document(node):
        result = {key: node[key] for key in ('id', 'directory', 'entry', 'managed', 'purpose', 'version')}
        if node['entry']:
            result['document'] = read_source([project], project_id, node['entry'])
        return result
    ancestors = []
    parent = target['parent']
    while parent:
        node = tree['nodes'][parent]
        if node['entry']:
            ancestors.append(document(node))
        parent = node['parent']
    return {'schema': 2, 'source_of_truth': 'Markdown files in project', 'project_id': project_id,
            'target': document(target), 'ancestors': list(reversed(ancestors)),
            'children': [document(tree['nodes'][i]) for i in target['children']],
            'notice': '先阅读祖先约束与本目录文档。完成后更新本页，影响上层范围、结论或下一步时核对父页。',
            'warnings': tree['warnings']}


def build_tree(projects, *, max_depth=3, max_nodes=350, max_entries_per_directory=3000,
               max_markdown_bytes=16_000_000):
    """Physical directories are the tree; protocol headers declare managed roles."""
    for name, value, minimum in (('max_depth', max_depth, 0), ('max_nodes', max_nodes, 1),
                                 ('max_entries_per_directory', max_entries_per_directory, 1),
                                 ('max_markdown_bytes', max_markdown_bytes, 1)):
        if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
            raise ValueError(f'{name} 必须是至少 {minimum} 的整数')
    output, nodes, warnings = [], {}, []
    project_ids = set()
    for spec in projects:
        if spec['id'] in project_ids:
            raise ValueError(f'项目 ID 重复: {spec["id"]}')
        project_ids.add(spec['id'])
        root = Path(spec['root']).absolute()
        project_path(root, '.', directory=True)
        project_nodes = []
        truncated = False
        markdown_bytes = 0
        def visit(directory, parent, depth):
            nonlocal truncated, markdown_bytes
            if len(project_nodes) >= max_nodes:
                truncated = True
                return None
            relative = directory.relative_to(root).as_posix()
            entries_incomplete = False
            metadata_incomplete = False
            try:
                entries = list(islice(directory.iterdir(), max_entries_per_directory + 1))
                if len(entries) > max_entries_per_directory:
                    entries = entries[:max_entries_per_directory]
                    entries_incomplete = True
                    metadata_incomplete = True
                    warnings.append(f'{spec["id"]}/{relative}: 达到每目录 {max_entries_per_directory} 项发现预算；文件和子目录数量仅为已发现部分')
                    # Keep conventional entry points readable even when their
                    # filesystem enumeration positions fall beyond the limit.
                    for name in ('README.md', 'DOCTREE.md'):
                        entry = directory / name
                        if entry not in entries and entry.is_file() and not _link(entry):
                            entries.append(entry)
                entries.sort(key=lambda p: p.name.casefold())
            except OSError as exc:
                warnings.append(f'{spec["id"]}/{relative}: {exc}')
                entries = []
                entries_incomplete = metadata_incomplete = True
            regular = [p for p in entries if not _link(p) and p.is_file()]
            dirs = [p for p in entries if not _link(p) and p.is_dir() and p.name.casefold() not in HIDDEN
                    and not p.name.startswith('.')]
            markdown = [p for p in regular if not p.name.startswith('.')
                        and p.suffix.lower() == '.md' and p.stat().st_size <= MAX_TEXT]
            markdown.sort(key=lambda p: (p.name.upper() != 'README.MD', p.name.upper() != 'DOCTREE.MD', p.name))
            managed = []
            primary = None
            body = ''
            for document in markdown:
                try:
                    remaining = max_markdown_bytes - markdown_bytes
                    if document.stat().st_size > remaining:
                        metadata_incomplete = True
                        warnings.append(f'{spec["id"]}/{relative}: 达到项目 Markdown 字节预算；目录说明未全部读取')
                        break
                    with document.open('rb') as stream:
                        raw = stream.read(min(MAX_TEXT, remaining) + 1)
                    if len(raw) > min(MAX_TEXT, remaining):
                        metadata_incomplete = True
                        warnings.append(f'{spec["id"]}/{document.name}: 读取期间大小超过预算；跳过此文档')
                        continue
                    markdown_bytes += len(raw)
                    text = raw.decode('utf-8-sig')
                    meta = parse_document(text)
                except (OSError, ValueError, UnicodeError) as exc:
                    metadata_incomplete = True
                    warnings.append(f'{spec["id"]}/{document.relative_to(root).as_posix()}: {exc}')
                    continue
                if primary is None and document.name.upper() in ('README.MD', 'DOCTREE.MD'):
                    primary, body = document, text
                if meta:
                    managed.append((document, text, meta))
            if len(managed) > 1:
                raise ValueError(f'{relative}: 一个目录只能有一份 DocTree 节点文档')
            meta = None
            if managed:
                primary, body, meta = managed[0]
            node_id = meta['id'] if meta else 'folder.' + hashlib.sha256(f'{spec["id"]}:{relative}'.encode()).hexdigest()[:20]
            if node_id in nodes:
                raise ValueError(f'目录节点 ID 重复: {node_id}')
            path = primary.relative_to(root).as_posix() if primary else None
            old_detail = next((n['id'] for n in spec.get('nodes', []) if n.get('entry') == path), None) if path else None
            is_bulk = directory.name.casefold() in BULK or directory.name.casefold().startswith(BULK_PREFIXES)
            collapsed = ('历史或批量产物目录，未展开内部；可沿 README 链接读取' if is_bulk and depth else
                         '已达到目录展开深度' if depth >= max_depth and dirs else None)
            node = {'id': node_id, 'project_id': spec['id'], 'parent': parent, 'children': [],
                    'title': meta['title'] if meta else (spec['title'] if relative == '.' else directory.name),
                    'directory': relative, 'entry': path, 'managed': bool(meta),
                    'purpose': meta.get('purpose', '') if meta else '尚未添加目录职责说明',
                    'version': hashlib.sha256(semantic_text(body).encode()).hexdigest() if body else None,
                    'detail_node_id': old_detail, 'file_count': len(regular), 'child_count': len(dirs),
                    'counts_partial': entries_incomplete, 'metadata_incomplete': metadata_incomplete,
                    'collapsed_reason': collapsed,
                    'source': 'markdown' if meta else 'directory',
                    'body': body if meta else '', 'readme_available': bool(primary),
                    'has_readme': any(p.name.casefold() == 'readme.md' for p in regular),
                    'readme_required': False}
            nodes[node_id] = node
            project_nodes.append(node_id)
            if not collapsed:
                for child in dirs:
                    child_id = visit(child, node_id, depth + 1)
                    if child_id:
                        node['children'].append(child_id)
                node['children'].sort(key=lambda child_id: (not nodes[child_id]['managed'], nodes[child_id]['title']))
                if len(node['children']) < len(dirs):
                    node['collapsed_reason'] = '节点数量达到上限，部分子目录未展开'
            return node_id
        root_id = visit(root, None, 0)
        output.append({'id': spec['id'], 'title': spec['title'], 'root': str(root), 'root_id': root_id,
                       'source_type': spec.get('source_type', 'local'), 'managed_count': sum(nodes[i]['managed'] for i in project_nodes),
                       'node_count': len(project_nodes)})
        if truncated:
            warnings.append(f'{spec["id"]}: 达到 {max_nodes} 个目录节点上限，显示为部分树')
    return {'schema': 2, 'protocol': 'markdown-first-v2', 'projects': output, 'nodes': nodes, 'warnings': warnings,
            'viewer': {'detailed_governance': False},
            'scanned_at': datetime.now(timezone.utc).isoformat(),
            'scan_policy': {'max_depth': max_depth, 'max_nodes_per_project': max_nodes,
                            'hidden_directories': sorted(HIDDEN), 'collapsed_bulk_directories': sorted(BULK),
                            'collapsed_bulk_prefixes': list(BULK_PREFIXES),
                            'max_entries_per_directory': max_entries_per_directory,
                            'max_markdown_bytes_per_project': max_markdown_bytes,
                            'source_of_truth': 'project Markdown; no governance database required'}}
