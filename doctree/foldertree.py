"""A disposable directory map projected from on-disk Markdown.

This module has no dependency on the legacy catalog, governance store or PyYAML.
"""
from __future__ import annotations

import hashlib
import codecs
import json
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


class _ContextReader:
    """Inspect individual directories, without walking any descendant tree."""

    def __init__(self, project, max_entries, max_bytes, max_body_chars):
        self.project = project
        self.root = Path(project['root']).absolute()
        self.max_entries, self.max_bytes = max_entries, max_bytes
        self.max_body_chars = max_body_chars
        self.bytes_read = 0
        self.entries = {}
        self.warnings = []
        self.warnings_omitted = 0
        self.entries_partial = self.source_partial = self.metadata_partial = False

    def warn(self, text):
        if len(self.warnings) < 8:
            self.warnings.append(str(text)[:240])
        else:
            self.warnings_omitted += 1

    def listing(self, directory):
        relative = directory.relative_to(self.root).as_posix()
        if relative in self.entries:
            return self.entries[relative]
        partial = False
        try:
            found = list(islice(directory.iterdir(), self.max_entries + 1))
            partial = len(found) > self.max_entries
            found = found[:self.max_entries]
        except OSError as exc:
            found, partial = [], True
            self.warn(f'{relative}: 目录读取失败: {exc}')
        if partial:
            self.entries_partial = True
            self.warn(f'{relative}: 目录条目未完整读取，最多检查 {self.max_entries} 项')
            # A selected path and conventional entry never depend on sibling order.
            for name in ('README.md', 'DOCTREE.md'):
                entry = directory / name
                if entry not in found and not _link(entry) and entry.is_file():
                    found.append(entry)
        found = [entry for entry in found if not entry.name.startswith('.')
                 and entry.name.casefold() not in HIDDEN and not _link(entry)]
        found.sort(key=lambda entry: (entry.name.casefold(), entry.name))
        self.entries[relative] = (found, partial)
        return found, partial

    def read_document(self, path):
        relative = path.relative_to(self.root).as_posix()
        limit = min(MAX_TEXT, self.max_bytes - self.bytes_read)
        if limit <= 0:
            self.source_partial = True
            return None
        try:
            checked = project_path(self.root, relative)
            with checked.open('rb') as stream:
                size = os.fstat(stream.fileno()).st_size
                raw = stream.read(limit)
            self.bytes_read += len(raw)
            partial = len(raw) < size
            decoder = codecs.getincrementaldecoder('utf-8-sig')('strict')
            text = decoder.decode(raw, final=not partial)
        except (OSError, UnicodeError, ValueError) as exc:
            self.warn(f'{relative}: 无法读取目录说明: {exc}')
            self.source_partial = True
            return None
        if partial:
            self.source_partial = True
            self.warn(f'{relative}: 来源读取达到字节预算；正文与版本信息不完整')
        metadata_partial = False
        try:
            meta = parse_document(text)
            version = hashlib.sha256(semantic_text(text).encode()).hexdigest() if not partial else None
        except ValueError as exc:
            self.warn(f'{relative}: 节点头部未完整解析: {exc}')
            meta, version = None, None
            metadata_partial = True
        return {'path': relative, 'text': text, 'meta': meta, 'version': version,
                'sha256': hashlib.sha256(raw).hexdigest() if len(raw) == size else None,
                'source_bytes': size, 'partial': partial, 'metadata_partial': metadata_partial}

    def describe(self, directory, *, with_body=False, target=False):
        relative = directory.relative_to(self.root).as_posix()
        entries, incomplete = self.listing(directory)
        candidates = [entry for entry in entries if entry.suffix.lower() == '.md' and entry.is_file()]
        candidates.sort(key=lambda entry: (entry.name.casefold() != 'readme.md',
                                           entry.name.casefold() != 'doctree.md', entry.name.casefold()))
        primary, chosen, managed = None, None, []
        for entry in candidates:
            if primary is None and entry.name.casefold() in ('readme.md', 'doctree.md'):
                primary = entry
            data = self.read_document(entry)
            if data is None:
                incomplete = True
                if self.bytes_read >= self.max_bytes:
                    self.warn(f'{relative}: 达到上下文 Markdown 读取预算，目录元数据可能不完整')
                    break
                continue
            incomplete = incomplete or data['partial'] or data['metadata_partial']
            if primary == entry:
                chosen = data
            if data['meta']:
                managed.append((entry, data))
        if len(managed) > 1:
            raise ValueError(f'{relative}: 一个目录只能有一份 DocTree 节点文档')
        if managed:
            primary, chosen = managed[0]
        meta = chosen['meta'] if chosen else None
        title = meta['title'] if meta else self.project['title'] if relative == '.' else directory.name
        purpose = meta.get('purpose', '') if meta else ''
        field_truncated = len(title) > 160 or len(purpose) > 800
        result = {'title': title[:160], 'directory': relative,
                  'entry': primary.relative_to(self.root).as_posix() if primary else None,
                  'purpose': purpose[:800]}
        if incomplete or field_truncated:
            result['metadata_incomplete'] = True
            self.metadata_partial = True
        if target:
            result.update({'id': meta['id'] if meta else 'folder.' + hashlib.sha256(
                f'{self.project["id"]}:{relative}'.encode()).hexdigest()[:20],
                'managed': bool(meta), 'version': chosen['version'] if chosen else None})
        if with_body and primary:
            text = chosen['text'] if chosen else ''
            partial = chosen is None or chosen['partial']
            content = text[:self.max_body_chars]
            result['document'] = {'path': result['entry'], 'content': content,
                                  'sha256': chosen['sha256'] if chosen else None,
                                  'source_bytes': chosen['source_bytes'] if chosen else None,
                                  'source_chars': len(text) if not partial else None,
                                  'content_chars': len(content), 'source_truncated': partial,
                                  'content_truncated': partial or len(content) < len(text)}
        return result


def _context_output_size(result):
    # The portable CLI uses this same human-readable serialization and newline.
    size = len(json.dumps(result, ensure_ascii=False, indent=2)) + 1
    while result['usage']['output_chars'] != size:
        result['usage']['output_chars'] = size
        size = len(json.dumps(result, ensure_ascii=False, indent=2)) + 1
    return size


def _bound_context(result, max_chars):
    """Keep target identity and entry intact; report every discarded payload."""
    def shorten_body(item, excess):
        document = item.get('document')
        if not document or not document['content']:
            return False
        document['content'] = document['content'][:max(0, len(document['content']) - excess)]
        document['content_chars'] = len(document['content'])
        document['content_truncated'] = True
        return True

    # Optional bodies yield first, then target prose, then whole directory records.
    optional = list(reversed(result['children'])) + list(reversed(result['ancestors']))
    while (size := _context_output_size(result)) > max_chars:
        result['truncation']['output'] = True
        excess = size - max_chars + 32
        if any(shorten_body(item, excess) for item in optional):
            continue
        if shorten_body(result['target'], excess):
            continue
        if result['children']:
            result['children'].pop()
            result['truncation']['children_omitted'] += 1
            result['usage']['children_returned'] = len(result['children'])
            continue
        if result['ancestors']:
            result['ancestors'].pop(1 if len(result['ancestors']) > 2 else 0)
            result['truncation']['ancestors_omitted'] += 1
            result['usage']['ancestors_returned'] = len(result['ancestors'])
            continue
        if result['warnings']:
            result['warnings'].pop()
            result['truncation']['warnings_omitted'] += 1
            continue
        raise ValueError('max_chars 不足以保留目标目录身份和路径，请增大上下文输出预算')
    return result


def context(projects, project_id, directory='.', *, body_scope='target', max_chars=24_000,
            max_body_chars=8_000, max_ancestors=8, max_children=20,
            max_entries_per_directory=3000, max_markdown_bytes=4_000_000):
    """Read an explicit path and its immediate context, independently of the UI.

    ``body_scope`` is ``target`` (default), ``none`` or ``all``. Ancestors and
    children otherwise contain only title, responsibility and project-relative
    paths. ``max_chars`` caps indented UTF-8 JSON characters, including its final
    newline; counts and truncation flags distinguish limits from complete reads.
    """
    for name, value, minimum in (('max_chars', max_chars, 4096), ('max_body_chars', max_body_chars, 0),
                                ('max_ancestors', max_ancestors, 0), ('max_children', max_children, 0),
                                ('max_entries_per_directory', max_entries_per_directory, 1),
                                ('max_markdown_bytes', max_markdown_bytes, 1)):
        if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
            raise ValueError(f'{name} 必须是至少 {minimum} 的整数')
    if not isinstance(body_scope, str) or body_scope not in ('target', 'none', 'all'):
        raise ValueError('body_scope 必须为 target、none 或 all')
    project = _project(projects, project_id)
    target_path = project_path(project['root'], directory, directory=True)
    reader = _ContextReader(project, max_entries_per_directory, max_markdown_bytes, max_body_chars)
    relative = target_path.relative_to(reader.root)
    ancestor_paths = [reader.root.joinpath(*relative.parts[:depth]) for depth in range(len(relative.parts))]
    if len(ancestor_paths) > max_ancestors:
        selected_ancestors = ([ancestor_paths[0]] + ancestor_paths[-(max_ancestors - 1):]
                              if max_ancestors > 1 else ancestor_paths[:max_ancestors])
    else:
        selected_ancestors = ancestor_paths
    target = reader.describe(target_path, with_body=body_scope != 'none', target=True)
    entries, entries_partial = reader.listing(target_path)
    child_paths = [entry for entry in entries if entry.is_dir()]
    ancestors = [reader.describe(path, with_body=body_scope == 'all') for path in selected_ancestors]
    children = [reader.describe(path, with_body=body_scope == 'all') for path in child_paths[:max_children]]
    result = {'schema': 2, 'context_format': 2, 'source_of_truth': 'Markdown files in project',
              'project_id': project_id, 'target': target, 'ancestors': ancestors, 'children': children,
              'budgets': {'body_scope': body_scope, 'max_chars': max_chars, 'max_body_chars': max_body_chars,
                          'max_ancestors': max_ancestors, 'max_children': max_children,
                          'max_entries_per_directory': max_entries_per_directory,
                          'max_markdown_bytes': max_markdown_bytes},
              'usage': {'output_chars': 0, 'markdown_bytes_read': reader.bytes_read,
                        'directories_read': len(reader.entries), 'ancestors_available': len(ancestor_paths),
                        'ancestors_returned': len(ancestors), 'children_discovered': len(child_paths),
                        'children_returned': len(children), 'child_count_complete': not entries_partial},
              'truncation': {'output': False, 'ancestors_omitted': len(ancestor_paths) - len(ancestors),
                             'children_omitted': len(child_paths) - len(children),
                             'directory_entries': reader.entries_partial, 'source_read': reader.source_partial,
                             'metadata': reader.metadata_partial, 'warnings_omitted': reader.warnings_omitted},
              'notice': '先阅读祖先职责与目标说明；需要祖先或子目录正文时显式读取入口或选择 body_scope=all。'
                        '完成本目录工作后更新本页，影响上层范围、结论或下一步时核对父页。',
              'warnings': reader.warnings}
    return _bound_context(result, max_chars)


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
