"""Small Git-based documentation checks. Decisions belong to people or Agents.

One shared JSON file records the baseline and the latest review for each node.
There is no watcher, task scheduler, model call, or automatic Markdown rewrite.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import uuid

from . import markdown_protocol as protocol

RECORD = 'doctree-review.json'
MAX_FILES = 4000
MAX_FILE_BYTES = 1_000_000
MAX_TOTAL_BYTES = 16_000_000


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode('utf-8')).hexdigest()


def _semantic(text):
    return protocol.semantic_text(text.lstrip('\ufeff')).replace('\r\n', '\n')


def _git(root, *args):
    try:
        process = subprocess.run(['git', '--no-optional-locks', '-c', 'core.fsmonitor=false',
                                  '-C', str(root), *args], capture_output=True, timeout=20)
    except subprocess.TimeoutExpired as exc:
        raise ValueError('Git 检查超时；本次不宣称检查完成。') from exc
    if process.returncode:
        raise ValueError('Git 检查失败；需要有提交历史的独立 Git 项目，且基线提交仍可读取。')
    if len(process.stdout) > 8_000_000:
        raise ValueError('Git 路径输出超过预算，请缩小检查范围。')
    return process.stdout.decode('utf-8')


def _root(root):
    root = protocol._root(root)
    top = Path(_git(root, 'rev-parse', '--show-toplevel').strip()).resolve()
    if top != root.resolve():
        raise ValueError('维护检查须在独立 Git 项目根运行，不能借用外层仓库的历史。')
    return root


def _read(root):
    path = protocol._safe(root, RECORD)
    if not path.exists():
        return None
    if path.stat().st_size > 4_000_000:
        raise ValueError('维护记录超过 4 MB，未覆盖；请先归档核对。')
    value = json.loads(path.read_text(encoding='utf-8-sig'))
    if (not isinstance(value, dict) or value.get('schema') != 1
            or not isinstance(value.get('base_commit'), str)
            or len(value['base_commit']) not in (40, 64)
            or any(c not in '0123456789abcdef' for c in value['base_commit'])
            or not isinstance(value.get('reviews'), dict)):
        raise ValueError('维护记录格式不支持或损坏，保留原文件。')
    for row in value['reviews'].values():
        if (not isinstance(row, dict) or not isinstance(row.get('token'), str)
                or row.get('decision') not in ('updated', 'no_change')
                or row.get('parent_impact') not in ('needed', 'not_needed')
                or not all(isinstance(row.get(k), str) and 0 < len(row[k].strip()) <= 2000
                           for k in ('reason', 'reviewer', 'parent_reason', 'document_version', 'at'))
                or not isinstance(row.get('child_inputs'), list)):
            raise ValueError('维护核对记录不完整，保留原文件。')
        for key in ('token', 'document_version'):
            if len(row[key]) != 64 or any(c not in '0123456789abcdef' for c in row[key]):
                raise ValueError('维护核对版本格式无效，保留原文件。')
        if any(not isinstance(child, dict) or not isinstance(child.get('node_id'), str)
               or not isinstance(child.get('token'), str) for child in row['child_inputs']):
            raise ValueError('子节点核对依据不完整，保留原文件。')
    return value


def _write(root, value):
    path = protocol._safe(root, RECORD)
    if path.exists():
        backup = root / '.doctree' / 'backups' / 'maintenance' / (uuid.uuid4().hex + '.json')
        protocol._check_ancestry(backup)
        backup.parent.mkdir(parents=True, exist_ok=True)
        backup.write_bytes(path.read_bytes())
    protocol._atomic_bytes(path, (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))


def initialize(root, *, base='HEAD'):
    """Establish a baseline; it does not claim earlier work was reviewed."""
    root = _root(root)
    with protocol._sync_lock(root, 10):
        existing = _read(root)
        if existing is not None:
            return {'status': 'already_initialized', 'base_commit': existing['base_commit'], 'record': RECORD}
        commit = _git(root, 'rev-parse', '--verify', '--end-of-options', base + '^{commit}').strip()
        value = {'schema': 1, 'base_commit': commit,
                 'initialized_at': datetime.now(timezone.utc).isoformat(), 'reviews': {}}
        _write(root, value)
    return {'status': 'initialized', 'base_commit': commit, 'record': RECORD,
            'notice': '只建立变化比较起点，不代表基线之前的代码或说明已经核对。'}


def _snapshot(root, state):
    paths = sorted(set(filter(None, _git(root, 'ls-files', '--cached', '--others',
                                          '--exclude-standard', '-z').split('\0'))))
    if len(paths) > MAX_FILES:
        raise ValueError('Git 可见文件超过 4000 项预算；本次不宣称检查完成。')
    excluded = set(protocol.DEFAULT_EXCLUDES)
    # These index flags can hide working-tree edits from Git diff. Inspect them
    # without refreshing the index or changing the user's chosen flags.
    hidden = [row[2:] for row in _git(root, 'ls-files', '-v', '-z').split('\0')
              if row and (row[0].islower() or row[0].upper() == 'S')
              and not any(part.casefold() in excluded for part in Path(row[2:]).parts)]
    if hidden:
        sample = ', '.join(hidden[:10])
        suffix = f'（共 {len(hidden)} 项）' if len(hidden) > 10 else ''
        raise ValueError('Git 索引的 assume-unchanged / skip-worktree 标志可能隐藏变化：'
                         f'{sample}{suffix}。本次检查未完成；请人工确认标志，工具未修改它们。')
    changes = set(filter(None, _git(root, 'diff', '--no-ext-diff', '--no-textconv',
                                   '--no-renames', '--name-only', '-z', state['base_commit'], '--').split('\0')))
    changes.update(filter(None, _git(root, 'ls-files', '--others', '--exclude-standard', '-z').split('\0')))
    changes.discard(RECORD)
    # Ignored/untracked files and technical internals are outside this opt-in loop.
    changes = {p for p in changes if not any(part.casefold() in excluded for part in Path(p).parts)}
    if len(changes) > MAX_FILES:
        raise ValueError('变化文件超过检查预算；本次不宣称检查完成。')
    raw_cache, total = {}, 0

    def read(path):
        nonlocal total
        if path in raw_cache:
            return raw_cache[path]
        relative = Path(path)
        if relative.is_absolute() or '..' in relative.parts or ':' in path or '\\' in path:
            raise ValueError('变化路径必须在 Git 项目内部')
        file = root / relative
        protocol._check_ancestry(file)
        if not file.exists():
            raw_cache[path] = None
            return None
        file = protocol._safe(root, path)
        if file.stat().st_size > MAX_FILE_BYTES:
            raise ValueError(f'文件超过维护读取预算（1 MB）：{path}')
        with file.open('rb') as stream:
            raw = stream.read(MAX_FILE_BYTES + 1)
        total += len(raw)
        if len(raw) > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
            raise ValueError('维护读取超过字节预算；本次不宣称检查完成。')
        raw_cache[path] = raw
        return raw

    nodes = []
    for path in paths:
        if not path.lower().endswith('.md') or any(part.casefold() in excluded for part in Path(path).parts):
            continue
        raw = read(path)
        if raw is None:
            continue
        try:
            text = raw.decode('utf-8-sig')
        except UnicodeError:
            # A protocol-looking non-UTF8 document cannot silently lose its node.
            if raw.lstrip(b'\xef\xbb\xbf \r\n\t').startswith(b'<!-- doctree:'):
                raise ValueError(f'节点文档须为 UTF-8：{path}')
            continue
        node = protocol.parse_document(text)
        if node:
            nodes.append({**node, 'path': path, 'entry': Path(path).name,
                          'directory': Path(path).parent.as_posix(),
                          'document_version': _hash(_semantic(text))})
    nodes = protocol._relationships(nodes)
    by_path = {n['path']: n for n in nodes}
    owners = sorted(nodes, key=lambda n: len(Path(n['directory']).parts), reverse=True)
    grouped = {n['id']: [] for n in nodes}
    unowned = []
    for path in sorted(changes):
        raw = read(path)
        if path in by_path and raw is not None:
            # Generated navigation alone is not a new documentation obligation.
            try:
                baseline = _git(root, 'show', state['base_commit'] + ':' + path)
            except ValueError:
                baseline = None  # A newly added node has no file at the baseline.
            if baseline is not None and _semantic(baseline) == _semantic(raw.decode('utf-8-sig')):
                continue
        owner = next((n for n in owners if n['directory'] == '.' or path.startswith(n['directory'] + '/')), None)
        if owner is None:
            unowned.append(path)
            continue
        token = (_hash(_semantic(raw.decode('utf-8-sig')))
                 if path in by_path and raw is not None else
                 hashlib.sha256(raw).hexdigest() if raw is not None else 'deleted')
        grouped[owner['id']].append({'path': path, 'version': token,
                                     'status': 'present' if raw is not None else 'deleted'})
    return nodes, grouped, unowned


def check(root):
    """Read current Git changes and reviewed tokens without writing anything."""
    root = _root(root)
    state = _read(root)
    if state is None:
        return {'schema': 1, 'status': 'not_initialized', 'complete': False, 'nodes': [],
                'notice': '先运行 review init 建立 Git 基线；此后 check 可发现已提交和未提交的变化。'}
    nodes, grouped, unowned = _snapshot(root, state)
    results, current_tokens = {}, {}
    # Child decisions are resolved first. A leaf does not automatically queue all ancestors.
    for node in sorted(nodes, key=lambda n: len(Path(n['directory']).parts), reverse=True):
        old = state['reviews'].get(node['id'])
        # Keep receipts for already absorbed versions. A new unreviewed leaf
        # must not retract those receipts and implicitly invalidate all parents.
        receipts = {row['node_id']: row for row in (old or {}).get('child_inputs', [])
                    if row['node_id'] in node['children']}
        requested = []
        for child_id in node['children']:
            child_review = state['reviews'].get(child_id)
            if (child_review and child_review['parent_impact'] == 'needed'
                    and child_review['token'] == current_tokens.get(child_id)):
                if receipts.get(child_id, {}).get('token') != child_review['token']:
                    requested.append({'node_id': child_id, 'token': child_review['token']})
                receipts[child_id] = {'node_id': child_id, 'token': child_review['token']}
        child_inputs = [receipts[key] for key in sorted(receipts)]
        token = _hash({'node_id': node['id'], 'directory': node['directory'],
                       'parent': node['parent'], 'document': node['document_version'],
                       'changes': grouped[node['id']], 'children': child_inputs})
        current_tokens[node['id']] = token
        current = bool(old and old['token'] == token)
        pending = bool(grouped[node['id']] or requested or old) and not current
        results[node['id']] = {'node_id': node['id'], 'directory': node['directory'],
                              'entry': node['path'], 'title': node['title'], 'parent': node['parent'],
                              'token': token, 'document_version': node['document_version'],
                              'status': 'needs_review' if pending else 'reviewed' if current else 'baseline',
                              'changes': grouped[node['id']], 'child_requests': requested, 'child_inputs': child_inputs,
                              'last_review': old}
    return {'schema': 1, 'status': 'checked', 'complete': True, 'record': RECORD,
            'base_commit': state['base_commit'], 'pending': sum(n['status'] == 'needs_review' for n in results.values()),
            'nodes': list(results.values()), 'unowned_changes': unowned,
            'notice': '变化仅提示核对。reviewed 是 Agent/人的文档声明，不代表业务验收；未作后台监听。'}


def acknowledge(root, *, node_id, token, decision, reason, reviewer, parent_impact, parent_reason):
    if decision not in ('updated', 'no_change') or parent_impact not in ('needed', 'not_needed'):
        raise ValueError('decision/parent_impact 必须明确选择')
    for label, value in [('reason', reason), ('reviewer', reviewer), ('parent_reason', parent_reason)]:
        if not isinstance(value, str) or not value.strip() or len(value) > 2000:
            raise ValueError(f'{label} 必须是 1–2000 字的明确说明')
    root = _root(root)
    with protocol._sync_lock(root, 10):
        report = check(root)
        if not report['complete']:
            raise ValueError('尚未建立基线，不能确认核对')
        node = next((n for n in report['nodes'] if n['node_id'] == node_id), None)
        if node is None or node['token'] != token:
            raise ValueError('输入版本已经变化或节点不存在，请重新检查实际差异')
        state = _read(root)
        review = {'token': token, 'decision': decision, 'reason': reason.strip(), 'reviewer': reviewer.strip(),
                  'parent_impact': parent_impact, 'parent_reason': parent_reason.strip(),
                  'document_version': node['document_version'], 'child_inputs': node['child_inputs']}
        old = state['reviews'].get(node_id)
        if old and all(old.get(k) == v for k, v in review.items()):
            return {'status': 'unchanged', 'node_id': node_id, 'record': RECORD}
        review['at'] = datetime.now(timezone.utc).isoformat()
        state['reviews'][node_id] = review
        _write(root, state)
    return {'status': 'recorded', 'node_id': node_id, 'record': RECORD,
            'notice': '已记录文档核对声明。仅在 parent_impact=needed 时提示直接父节点，继续逐级判断。'}


def attach(tree, projects):
    """Optional read-only status for existing opt-in records; never initialize."""
    for project in projects:
        if not (Path(project['root']) / RECORD).is_file():
            continue
        try:
            report = check(project['root'])
            by_id = {row['node_id']: row for row in report['nodes']}
            for node in tree['nodes'].values():
                if node['project_id'] == project['id'] and node['id'] in by_id:
                    row = by_id[node['id']]
                    node['maintenance'] = {k: row[k] for k in ('status', 'changes', 'child_requests')}
            tree.setdefault('maintenance', {})[project['id']] = {
                'pending': report.get('pending', 0), 'complete': report['complete'],
                'unowned_changes': report.get('unowned_changes', [])}
        except (ValueError, OSError, subprocess.SubprocessError) as exc:
            tree['warnings'].append(f'{project["id"]}: 文档核对检查未完成：{exc}')
            tree.setdefault('maintenance', {})[project['id']] = {'complete': False, 'error': str(exc)}
    return tree
