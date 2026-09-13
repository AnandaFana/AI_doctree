"""Markdown-first directory nodes and explicit, recoverable navigation writes.

Discovery is read-only. Only ``apply_plan`` writes, after validating the complete
input snapshot. A journal and original-byte backups make interrupted multi-file
writes inspectable; they do not claim filesystem-wide atomicity.
"""
from __future__ import annotations

from datetime import datetime, timezone
from contextlib import contextmanager
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import time
from urllib.parse import quote
import uuid


SCHEMA_VERSION = 2
ID_PATTERN = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$')
NODE_PREFIX = '<!-- doctree:node '
PURPOSE_START = '<!-- doctree:purpose:start -->'
PURPOSE_END = '<!-- doctree:purpose:end -->'
NAV_START = '<!-- doctree:nav:start -->'
NAV_END = '<!-- doctree:nav:end -->'
DEFAULT_EXCLUDES = {'.git', '.doctree', '.venv', 'venv', 'node_modules', 'vendor',
                    'deps', '__pycache__', '.pytest_cache', '.mypy_cache',
                    '.ruff_cache', '.cache', '.tox', '.nox'}
DISCOVERY_DEFAULTS = {'max_depth': 32, 'max_files': 20_000, 'max_directories': 20_000,
                      'max_file_bytes': 1_000_000, 'max_total_bytes': 64_000_000}


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def generated_node_id(project_id, directory):
    """Generate a new identity; existing annotated IDs never use this helper.

    The hash includes the complete project identity and normalized directory, so
    separators, spaces and long project prefixes cannot collapse distinct paths.
    """
    if not isinstance(project_id, str) or not ID_PATTERN.fullmatch(project_id):
        raise ValueError('project_id 格式无效')
    relative = Path(str(directory).replace('\\', '/'))
    if relative.is_absolute() or '..' in relative.parts or ':' in str(relative):
        raise ValueError('生成节点 id 需要项目内相对目录')
    normalized = relative.as_posix()
    if normalized == '.':
        return project_id
    fingerprint = _hash((project_id + '\0' + normalized).encode('utf-8'))[:16]
    return project_id[:130] + '.d.' + fingerprint


def _is_link(path):
    info = path.lstat()
    return (stat.S_ISLNK(info.st_mode)
            or bool(getattr(info, 'st_file_attributes', 0)
                    & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400)))


def _check_ancestry(path):
    """Also reject linked ancestors above the selected project root."""
    for part in (*reversed(path.parents), path):
        if part.exists() or part.is_symlink():
            if _is_link(part):
                raise ValueError(f'不跟随符号链接、目录联接或重解析点: {part}')


def _root(path):
    result = Path(os.path.abspath(path))
    _check_ancestry(result)
    if not result.is_dir():
        raise ValueError(f'项目目录不存在: {result}')
    return result


def _safe(root, relative, *, directory=False):
    value = str(relative).replace('\\', '/')
    rel = Path(value)
    if (rel.is_absolute() or '..' in rel.parts or ':' in value
            or any(part.casefold() in {'.git', '.doctree'} for part in rel.parts)):
        raise ValueError(f'路径必须在项目普通目录内: {relative}')
    target = root / rel
    _check_ancestry(target)
    try:
        target.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f'路径超出项目: {relative}') from exc
    if directory:
        if not target.is_dir():
            raise ValueError(f'接入目录不存在: {relative}')
    elif target.exists():
        info = target.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink > 1:
            raise ValueError(f'文档不是独立普通文件（可能有硬链接）: {relative}')
    elif not target.parent.is_dir():
        raise ValueError(f'文档所在目录不存在: {relative}')
    return target


def _single_line(value, field):
    if not isinstance(value, str) or not value.strip() or '\n' in value or '\r' in value:
        raise ValueError(f'{field} 必须是非空单行文本')
    if '<!-- doctree:' in value or '-->' in value:
        raise ValueError(f'{field} 不允许包含协议标记')
    return value


def _layout(text):
    """Parse only a leading header, leaving protocol examples in the body alone."""
    if not isinstance(text, str):
        raise TypeError('Markdown 必须是文本')
    start = len(text) - len(text.lstrip('\ufeff \t\r\n'))
    if not text.startswith('<!-- doctree:', start):
        return None
    if not text.startswith(NODE_PREFIX, start):
        raise ValueError('文档开头存在不完整或错位的 doctree 标记')
    end = text.find('-->', start)
    if end < 0:
        raise ValueError('doctree:node 标记未闭合')
    try:
        node = json.loads(text[start + len(NODE_PREFIX):end].strip())
    except json.JSONDecodeError as exc:
        raise ValueError('doctree:node JSON 无法解析') from exc
    if not isinstance(node, dict) or node.get('schema') != SCHEMA_VERSION:
        raise ValueError('不支持的 Markdown 节点 schema')
    if not isinstance(node.get('id'), str) or not ID_PATTERN.fullmatch(node['id']):
        raise ValueError('节点 id 格式无效')
    _single_line(node.get('title'), 'title')
    if 'parent' in node or 'children' in node:
        raise ValueError('schema 2 父子关系由目录推导，不写入机器声明')
    identity_end = end + 3

    def region(cursor, opening, closing):
        begin = cursor + len(text[cursor:]) - len(text[cursor:].lstrip(' \t\r\n'))
        if not text.startswith(opening, begin):
            raise ValueError(f'缺失或错位的标记: {opening}')
        stop = text.find(closing, begin + len(opening))
        if stop < 0:
            raise ValueError(f'未闭合的标记: {opening}')
        inner = text[begin + len(opening):stop]
        if '<!-- doctree:' in inner:
            raise ValueError('重复或交错的 doctree 标记')
        return (begin, stop + len(closing), inner)

    purpose = region(identity_end, PURPOSE_START, PURPOSE_END)
    nav = region(purpose[1], NAV_START, NAV_END)
    tail = text[nav[1]:].lstrip(' \t\r\n')
    if tail.startswith('<!-- doctree:'):
        raise ValueError('重复的 doctree 头部标记')
    purpose_lines = [re.sub(r'^\s*> ?', '', line).strip()
                     for line in purpose[2].strip().splitlines()]
    purpose_text = '\n'.join(purpose_lines)
    purpose_text = re.sub(r'^本目录职责[：:]\s*', '', purpose_text)
    return {'node': {**node, 'purpose': purpose_text}, 'identity': (start, identity_end),
            'purpose': purpose, 'nav': nav}


def parse_document(text):
    """Return schema-2 identity and human responsibility, or None if unmanaged."""
    layout = _layout(text)
    return layout['node'] if layout else None


def semantic_text(text):
    """Remove only the validated leading generated navigation region."""
    layout = _layout(text)
    if not layout:
        return text
    start, end, _ = layout['nav']
    return text[:start] + text[end:]


def _relationships(nodes):
    by_dir, ids = {}, set()
    for node in nodes:
        key = os.path.normcase(node['directory'])
        if key in by_dir:
            raise ValueError(f'同一目录存在多个节点文档: {node["directory"]}')
        if node['id'] in ids:
            raise ValueError(f'重复节点 id: {node["id"]}')
        by_dir[key] = node
        ids.add(node['id'])
        node['parent'], node['children'] = None, []
    for node in nodes:
        directory = Path(node['directory'])
        if str(directory) == '.':
            continue
        for parent in directory.parents:
            parent_node = by_dir.get(os.path.normcase(parent.as_posix()))
            if parent_node:
                node['parent'] = parent_node['id']
                parent_node['children'].append(node['id'])
                break
    return nodes


def discover_nodes(root, doc_names=('README.md', 'DOCTREE.md'), *, max_depth=32,
                   max_files=20_000, max_file_bytes=1_000_000, max_directories=20_000,
                   max_total_bytes=64_000_000, exclude_dirs=None, _stats=None):
    """Find leading nodes in bounded Markdown discovery, including custom names.

    Excluded directories are not descended. Exhausted budgets raise errors so a
    partial traversal cannot silently delete links during a subsequent sync.
    """
    for name, value, minimum in (('max_depth', max_depth, 0), ('max_files', max_files, 1),
                                 ('max_file_bytes', max_file_bytes, 1),
                                 ('max_directories', max_directories, 1),
                                 ('max_total_bytes', max_total_bytes, 1)):
        if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
            raise ValueError(f'{name} 必须是至少 {minimum} 的整数')
    root = _root(root)
    if exclude_dirs is not None and (not isinstance(exclude_dirs, (list, tuple, set))
                                     or any(not isinstance(name, str) for name in exclude_dirs)):
        raise ValueError('exclude_dirs 必须是目录名称数组')
    excluded = {name.casefold() for name in DEFAULT_EXCLUDES}
    excluded.update(str(name).casefold() for name in (exclude_dirs or ()))
    nodes, file_count, dir_count, total_bytes = [], 0, 0, 0
    preferred = {name.casefold() for name in doc_names}

    def fail_walk(error):
        raise ValueError(f'无法完整读取目录，拒绝生成部分导航: {error}') from error

    for current, dirs, files in os.walk(root, followlinks=False, onerror=fail_walk):
        folder = Path(current)
        dir_count += 1
        if dir_count > max_directories:
            raise ValueError('目录发现超过预算，未生成不完整导航')
        depth = len(folder.relative_to(root).parts)
        safe_dirs = []
        for name in sorted(dirs):
            child = folder / name
            if name.casefold() not in excluded and not _is_link(child):
                safe_dirs.append(name)
        if depth >= max_depth and safe_dirs:
            raise ValueError(f'目录深度超过预算: {folder.relative_to(root)}')
        dirs[:] = safe_dirs
        candidates = sorted((name for name in files if name.lower().endswith('.md')),
                            key=lambda name: (name.casefold() not in preferred, name.casefold()))
        for name in candidates:
            relative = (folder / name).relative_to(root).as_posix()
            path = _safe(root, relative)
            file_count += 1
            if file_count > max_files:
                raise ValueError('Markdown 文件发现超过预算')
            if path.stat().st_size > max_file_bytes:
                raise ValueError(f'Markdown 文件超过大小预算: {relative}')
            try:
                remaining = max_total_bytes - total_bytes
                if path.stat().st_size > remaining:
                    raise ValueError('Markdown 总字节数超过发现预算')
                with path.open('rb') as stream:
                    data = stream.read(min(max_file_bytes, remaining) + 1)
                if len(data) > min(max_file_bytes, remaining):
                    raise ValueError('读取期间 Markdown 大小超过发现预算')
                total_bytes += len(data)
                node = parse_document(data.decode('utf-8'))
            except UnicodeDecodeError as exc:
                raise ValueError(f'Markdown 需为 UTF-8，未改写: {relative}') from exc
            except ValueError as exc:
                raise ValueError(f'{relative}: {exc}') from exc
            if node:
                nodes.append({**node, 'directory': folder.relative_to(root).as_posix(),
                              'entry': name, 'path': relative, 'sha256': _hash(data),
                              'semantic_sha256': _hash(semantic_text(data.decode('utf-8')).encode('utf-8'))})
    if _stats is not None:
        _stats.update(files=file_count, directories=dir_count, bytes=total_bytes)
    return _relationships(sorted(nodes, key=lambda item: item['path']))


def _label(value):
    return (value.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            .replace('\\', '\\\\').replace('[', '\\[').replace(']', '\\]'))


def _link(source, target):
    relative = os.path.relpath(target['path'], start=source['directory']).replace('\\', '/')
    return f'[{_label(target["title"])}]({quote(relative, safe="/.-_~")})'


def _nav(node, nodes, newline):
    by_id = {item['id']: item for item in nodes}
    parent = _link(node, by_id[node['parent']]) if node['parent'] else '无（项目入口）'
    children = ' · '.join(_link(node, by_id[child]) for child in node['children']) or '暂无已接入子目录'
    return newline.join([NAV_START, f'> 上级：{parent}', f'> 子目录：{children}',
                         '> 更新约定：先更新本目录；影响范围、结论或下一步时核对上级 README。执行完成与业务/科学验收分别记录。',
                         NAV_END])


def _identity(node):
    value = {key: node[key] for key in ('schema', 'id', 'title')}
    return NODE_PREFIX + json.dumps(value, ensure_ascii=False, separators=(',', ':')) + ' -->'


def _generated_newline(text):
    """Use a standard newline for new text, without normalizing source bytes."""
    match = re.search(r'[\r\n]+', text)
    return '\r\n' if match and '\r' in match.group(0) else '\n'


def _render(before, node, nodes, update):
    newline = _generated_newline(before)
    layout = _layout(before)
    navigation = _nav(node, nodes, newline)
    responsibility = newline.join([PURPOSE_START, f'> 本目录职责：{node["purpose"]}', PURPOSE_END])
    if not layout:
        bom = '\ufeff' if before.startswith('\ufeff') else ''
        body = before[len(bom):]
        return bom + newline.join([_identity(node), responsibility, navigation]) + newline * 2 + body
    replacements = [(layout['nav'][0], layout['nav'][1], navigation)]
    if 'title' in update:
        replacements.append((*layout['identity'], _identity(node)))
    if 'purpose' in update:
        replacements.append((layout['purpose'][0], layout['purpose'][1], responsibility))
    after = before
    for start, end, replacement in sorted(replacements, reverse=True):
        after = after[:start] + replacement + after[end:]
    return after


def plan_sync(root, selections, project_id, *, discovery_options=None):
    """Build a JSON plan; selected directories already exist and are not moved."""
    root = _root(root)
    if not isinstance(project_id, str) or not ID_PATTERN.fullmatch(project_id):
        raise ValueError('project_id 格式无效')
    options = dict(discovery_options or {})
    if set(options) - (set(DISCOVERY_DEFAULTS) | {'exclude_dirs'}):
        raise ValueError('存在不支持的节点发现选项')
    if isinstance(options.get('exclude_dirs'), (tuple, set)):
        options['exclude_dirs'] = sorted(options['exclude_dirs'])
    discovery_stats = {}
    nodes = discover_nodes(root, **options, _stats=discovery_stats)
    original_paths = sorted(node['path'] for node in nodes)
    by_dir = {os.path.normcase(node['directory']): node for node in nodes}
    updates, selected_dirs = {}, set()
    for selection in selections:
        if not isinstance(selection, dict):
            raise ValueError('每个接入选择必须是对象')
        directory = _safe(root, selection.get('directory', '.'), directory=True)
        relative_dir = directory.relative_to(root).as_posix()
        excluded = {name.casefold() for name in DEFAULT_EXCLUDES}
        excluded.update(str(name).casefold() for name in options.get('exclude_dirs', []))
        if any(part.casefold() in excluded for part in Path(relative_dir).parts):
            raise ValueError('选择目录属于发现排除范围，无法维护完整导航')
        key = os.path.normcase(relative_dir)
        if key in selected_dirs:
            raise ValueError(f'重复接入目录: {relative_dir}')
        selected_dirs.add(key)
        entry = str(selection.get('entry', 'README.md'))
        if (Path(entry).name != entry or '/' in entry or '\\' in entry
                or not entry.casefold().endswith('.md')):
            raise ValueError('entry 必须是目录内的 Markdown 文件名')
        relative_path = (directory / entry).relative_to(root).as_posix()
        path = _safe(root, relative_path)
        if 'initial_body' in selection:
            if not isinstance(selection['initial_body'], str):
                raise ValueError('initial_body 必须是文本')
            if path.exists() and selection['initial_body']:
                raise ValueError('initial_body 仅允许用于尚不存在的新文档')
            if _layout(selection['initial_body']):
                raise ValueError('initial_body 不可包含已有节点头部')
            if len(selection['initial_body'].encode('utf-8')) > options.get('max_file_bytes', DISCOVERY_DEFAULTS['max_file_bytes']):
                raise ValueError('initial_body 超过文档大小预算')
        node = by_dir.get(key)
        if node and os.path.normcase(node['path']) != os.path.normcase(relative_path):
            raise ValueError(f'此目录已使用 {node["entry"]}；移动入口需先显式移动文件')
        if node:
            if 'id' in selection and selection['id'] != node['id']:
                raise ValueError('已有节点 id 不可通过 sync 改写')
        else:
            node_id = selection.get('id', generated_node_id(project_id, relative_dir))
            if not isinstance(node_id, str) or not ID_PATTERN.fullmatch(node_id):
                raise ValueError('节点 id 格式无效；较长目录可显式指定较短 id')
            node = {'schema': SCHEMA_VERSION, 'id': node_id, 'directory': relative_dir,
                    'entry': entry, 'path': relative_path,
                    'title': selection.get('title', directory.name),
                    'purpose': selection.get('purpose', '待人或 Agent 补充本目录职责。')}
            nodes.append(node)
            by_dir[key] = node
        for field in ('title', 'purpose'):
            if field in selection:
                node[field] = _single_line(selection[field], field)
        _single_line(node['title'], 'title')
        updates[node['id']] = selection
    nodes = _relationships(sorted(nodes, key=lambda item: item['path']))
    changes, inputs = [], []
    projected_files, projected_bytes = discovery_stats['files'], discovery_stats['bytes']
    for node in nodes:
        path = _safe(root, node['path'])
        raw = path.read_bytes() if path.exists() else None
        try:
            before = raw.decode('utf-8') if raw is not None else ''
        except UnicodeDecodeError as exc:
            raise ValueError(f'文档需为 UTF-8，未改写: {node["path"]}') from exc
        expected = _hash(raw) if raw is not None else None
        inputs.append({'path': node['path'], 'expected_sha256': expected})
        update = updates.get(node['id'], {})
        initial_body = update.get('initial_body', '') if raw is None else ''
        after = _render(initial_body if raw is None else before, node, nodes, update)
        if len(after.encode('utf-8')) > options.get('max_file_bytes', DISCOVERY_DEFAULTS['max_file_bytes']):
            raise ValueError(f'生成节点文档后超过文件大小预算: {node["path"]}')
        projected_files += int(raw is None)
        projected_bytes += len(after.encode('utf-8')) - (len(raw) if raw is not None else 0)
        node['sha256'] = _hash(after.encode('utf-8'))
        node['semantic_sha256'] = _hash(semantic_text(after).encode('utf-8'))
        if raw is None or after != before:
            changes.append({'path': node['path'], 'expected_sha256': expected,
                            'before': before if raw is not None else None, 'after': after,
                            'kind': 'create' if raw is None else 'update',
                            **({'initial_body': initial_body} if raw is None else {})})
    if projected_files > options.get('max_files', DISCOVERY_DEFAULTS['max_files']):
        raise ValueError('同步结果超过 Markdown 文件发现预算，未生成部分计划')
    if projected_bytes > options.get('max_total_bytes', DISCOVERY_DEFAULTS['max_total_bytes']):
        raise ValueError('同步结果超过 Markdown 总字节预算，未生成部分计划')
    return {'schema': 1, 'protocol_schema': SCHEMA_VERSION, 'root': str(root),
            'project_id': project_id, 'created_at': datetime.now(timezone.utc).isoformat(),
            'discovery_options': options,
            'managed_paths_before': original_paths, 'inputs': inputs,
            'changes': changes, 'nodes': nodes}


def _atomic_bytes(path, data, mode=None):
    fd, temp_name = tempfile.mkstemp(prefix='.doctree-write-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if mode is not None:
            os.chmod(temp_name, mode)
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


class SyncApplyError(RuntimeError):
    def __init__(self, message, report):
        super().__init__(message)
        self.report = report


@contextmanager
def _sync_lock(root, timeout):
    """Cooperating writers share an OS lock; the lock file is never unlinked.

    A process crash releases the OS lock, so an old file is not a stale owner.
    Keeping its inode avoids splitting locks between a waiter and a new writer.
    """
    if timeout < 0:
        raise ValueError('同步锁等待时间不可为负数')
    lock_dir = root / '.doctree'
    _check_ancestry(lock_dir)
    lock_dir.mkdir(exist_ok=True)
    _check_ancestry(lock_dir)
    path = lock_dir / 'sync.lock'
    _check_ancestry(path)
    flags = os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0)
    fd = os.open(path, flags, 0o600)
    locked = False
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError('同步锁必须是没有硬链接的普通文件')
        _check_ancestry(path)
        if not os.path.samestat(info, path.stat()):
            raise ValueError('同步锁路径在打开期间发生变化')
        if info.st_size == 0:
            os.write(fd, b'0')
        started = time.monotonic()
        if os.name == 'nt':
            import msvcrt
        else:
            import fcntl
        while True:
            try:
                os.lseek(fd, 0, os.SEEK_SET)
                if os.name == 'nt':
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                else:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
                break
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                    raise
                if time.monotonic() - started >= timeout:
                    raise ValueError(f'同步锁等待超时；另一个同步仍在写入，未改动文档: {path}') from exc
                time.sleep(min(0.05, max(0.001, timeout - (time.monotonic() - started))))
        yield
    finally:
        try:
            if locked:
                os.lseek(fd, 0, os.SEEK_SET)
                if os.name == 'nt':
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def apply_plan(plan, backup_dir=None, *, lock_timeout=10.0):
    """Apply a reviewed plan with all-input preflight, backups, and a journal.

    Stale inputs cause zero document writes. A later filesystem failure raises
    SyncApplyError with a report and leaves original backups plus the journal.
    Recovery should inspect current hashes before restoring individual files.
    """
    if plan.get('schema') != 1 or plan.get('protocol_schema') != SCHEMA_VERSION:
        raise ValueError('不支持的同步计划版本')
    root = _root(plan['root'])
    with _sync_lock(root, lock_timeout):
        return _apply_plan_locked(plan, root, backup_dir)


def _apply_plan_locked(plan, root, backup_dir):
    actual_paths = sorted(node['path'] for node in discover_nodes(root, **plan.get('discovery_options', {})))
    if actual_paths != plan.get('managed_paths_before'):
        raise ValueError('计划已过期：已接入文档集合发生变化，请重新生成计划')
    snapshots, seen = {}, set()
    for item in plan['inputs']:
        path = _safe(root, item['path'])
        key = os.path.normcase(str(path))
        if key in seen:
            raise ValueError('同步计划含重复输入路径')
        seen.add(key)
        raw = path.read_bytes() if path.exists() else None
        if (_hash(raw) if raw is not None else None) != item['expected_sha256']:
            raise ValueError(f'计划已过期，未写入任何文档: {item["path"]}')
        snapshots[item['path']] = (path, raw)
    seen.clear()
    for change in plan['changes']:
        path = _safe(root, change['path'])
        key = os.path.normcase(str(path))
        if key in seen or change['path'] not in snapshots:
            raise ValueError('同步计划含重复或未经快照验证的写入')
        seen.add(key)
        raw = snapshots[change['path']][1]
        if (_hash(raw) if raw is not None else None) != change['expected_sha256']:
            raise ValueError('写入版本与输入版本不一致')
        after = change['after']
        after_layout = _layout(after)
        if not after_layout:
            raise ValueError('同步结果缺少合法节点头部')
        before = raw.decode('utf-8') if raw is not None else change.get('initial_body', '')
        if not isinstance(before, str):
            raise ValueError('新文档 initial_body 必须是文本')
        before_layout = _layout(before)
        if raw is None and before_layout:
            raise ValueError('新文档 initial_body 不可包含已有节点头部')
        if before_layout:
            if (before[:before_layout['identity'][0]] != after[:after_layout['identity'][0]]
                    or before[before_layout['nav'][1]:] != after[after_layout['nav'][1]:]):
                raise ValueError('同步计划不可改写头部以外的原始正文')
            if before_layout['node']['id'] != after_layout['node']['id']:
                raise ValueError('同步计划不可改变已有稳定 id')
        else:
            bom = '\ufeff' if before.startswith('\ufeff') else ''
            newline = _generated_newline(before)
            if (after[:after_layout['identity'][0]] != bom
                    or after[after_layout['nav'][1]:] != newline * 2 + before[len(bom):]):
                raise ValueError('首次注入不可改写原始正文')
    report = {'root': str(root), 'status': 'unchanged', 'written': [],
              'backup_dir': None, 'journal': None, 'changes': len(plan['changes'])}
    if not plan['changes']:
        return report
    base = Path(backup_dir) if backup_dir is not None else root / '.doctree' / 'backups'
    base = Path(os.path.abspath(base))
    _check_ancestry(base)
    base.mkdir(parents=True, exist_ok=True)
    run_dir = base / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '-' + uuid.uuid4().hex[:12])
    run_dir.mkdir()
    report.update(status='prepared', backup_dir=str(run_dir), journal=str(run_dir / 'journal.json'))
    journal = {**report, 'files': []}

    def save_journal():
        _atomic_bytes(run_dir / 'journal.json', (json.dumps(journal, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))

    try:
        for change in plan['changes']:
            path, raw = snapshots[change['path']]
            backup = None
            if raw is not None:
                backup_path = run_dir / 'originals' / change['path']
                backup_path.parent.mkdir(parents=True, exist_ok=True)
                _atomic_bytes(backup_path, raw)
                backup = str(backup_path)
            journal['files'].append({'path': change['path'], 'before_sha256': change['expected_sha256'],
                                     'after_sha256': _hash(change['after'].encode('utf-8')),
                                     'backup': backup, 'status': 'pending'})
        save_journal()
        # Recheck the complete input set after backups, before the first write.
        for relative, (path, raw) in snapshots.items():
            _safe(root, relative)
            current = path.read_bytes() if path.exists() else None
            if current != raw:
                raise ValueError(f'备份期间输入发生变化: {relative}')
        for change, record in zip(plan['changes'], journal['files']):
            path, raw = snapshots[change['path']]
            _safe(root, change['path'])
            current = path.read_bytes() if path.exists() else None
            if current != raw:
                raise ValueError(f'写入前输入发生变化: {change["path"]}')
            mode = stat.S_IMODE(path.stat().st_mode) if raw is not None else None
            _atomic_bytes(path, change['after'].encode('utf-8'), mode)
            record['status'] = 'written'
            report['written'].append(change['path'])
            journal['written'] = list(report['written'])
            save_journal()
        report['status'] = journal['status'] = 'applied'
        save_journal()
        return report
    except Exception as exc:
        report['status'] = journal['status'] = 'partial' if report['written'] else 'failed_before_write'
        journal['error'] = str(exc)
        try:
            save_journal()
        except OSError:
            pass
        raise SyncApplyError(f'同步未完整完成，保留原文备份和恢复记录: {run_dir}; {exc}', report) from exc
