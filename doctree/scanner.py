"""Bounded, non-executing source discovery. A map never relocates source files."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
from datetime import datetime, timezone

import yaml
from .markdown_protocol import parse_document, semantic_text

DEFAULT_EXCLUDES = {'.git', '.venv', 'venv', 'node_modules', '__pycache__', '.pytest_cache',
                    '.doctree', 'build', 'dist', 'runs', 'records', 'output', 'history'}
TEXT_EXTENSIONS = {'.md', '.txt', '.rst', '.json', '.yaml', '.yml', '.toml', '.tex', '.py', '.csv'}
GENERATED = re.compile(r'<!-- doctree:generated:start -->.*?<!-- doctree:generated:end -->', re.S)
BLOCK = re.compile(r'^```(?:yaml|yml|json)\s*\n(.*?)^```\s*$', re.M | re.S)
ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$')
SCANNER_VERSION = 'doctree-scan-v2'


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
    return hashlib.sha256(data).hexdigest()


def file_hash(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def is_link(path):
    return path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction())


def safe_path(root, relative, must_exist=True):
    root = Path(root).absolute()
    rel = Path(str(relative).replace('\\', '/'))
    if rel.is_absolute() or '..' in rel.parts or ':' in str(rel):
        raise ValueError(f'路径必须在项目内: {relative}')
    target = root / rel
    if '.git' in rel.parts or '.doctree' in rel.parts:
        raise ValueError('不允许读取治理内部或 Git 内部文件')
    for parent in (root, *target.parents, target):
        if parent == root.parent:
            break
        if is_link(parent):
            raise ValueError(f'不跟随符号链接或目录联接: {relative}')
    try:
        target.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f'路径超出项目范围: {relative}') from exc
    if must_exist and not target.is_file():
        raise ValueError(f'文件不存在: {relative}')
    return target


def canonical_text(text):
    return GENERATED.sub('', semantic_text(text)).replace('\r\n', '\n').strip()


def metadata(text, path):
    lightweight = parse_document(text)
    if lightweight:
        return {**lightweight, 'review': 'candidate', '_markdown_first': True}
    result = None
    text = GENERATED.sub('', text)
    for match in BLOCK.finditer(text):
        if 'project_node' not in match.group(1):
            continue
        try:
            parsed = yaml.safe_load(match.group(1))
        except yaml.YAMLError as exc:
            raise ValueError(f'{path}: 节点 YAML 无法解析') from exc
        if not isinstance(parsed, dict) or 'project_node' not in parsed:
            continue
        if result is not None:
            raise ValueError(f'{path}: 只能有一个 project_node 区块')
        result = parsed['project_node']
        if not isinstance(result, dict) or result.get('schema') != 1:
            raise ValueError(f'{path}: 不支持的节点 schema')
    return result


def git_info(root):
    env = dict(os.environ, GIT_OPTIONAL_LOCKS='0')
    def git(*args):
        p = subprocess.run(['git', '-C', str(root), *args], capture_output=True,
                           encoding='utf-8', errors='replace', env=env, timeout=20)
        return p.stdout.strip() if p.returncode == 0 else None
    try:
        top = git('rev-parse', '--show-toplevel')
        # A plain project inside the tool checkout must not inherit that Git identity.
        if not top or Path(top).resolve() != Path(root).resolve():
            return {'head': None, 'dirty': None, 'status': None}
        status = git('status', '--porcelain=v1', '--untracked-files=all')
        return {'head': git('rev-parse', 'HEAD'), 'dirty': bool(status), 'status': status,
                'git_dir': git('rev-parse', '--absolute-git-dir')}
    except (OSError, subprocess.TimeoutExpired):
        return {'head': None, 'dirty': None, 'status': None, 'warning': 'Git 状态不可用'}


def load_config(path):
    config_path = Path(path).resolve()
    config = json.loads(config_path.read_text(encoding='utf-8-sig'))
    if config.get('schema') != 1 or not isinstance(config.get('projects'), list):
        raise ValueError('项目配置必须使用 schema 1 和 projects 数组')
    workspace = config_path.parent.parent
    return config, workspace


def scan(config_path):
    scanned_at_ns = time.time_ns()
    config, workspace = load_config(config_path)
    nodes, projects, warnings = {}, [], []
    legacy_children = {}
    for spec in config['projects']:
        if any(p['id'] == spec['id'] for p in projects):
            raise ValueError(f'重复项目 ID: {spec["id"]}')
        root = (workspace / spec['root']).absolute()
        if not root.is_dir() or is_link(root):
            raise ValueError(f'项目根目录不存在或为链接: {root}')
        policy = {'max_files': 3000, 'max_file_bytes': 1000000, 'max_depth': 12,
                  **spec.get('scan', {})}
        excludes = DEFAULT_EXCLUDES | set(policy.get('exclude_dirs', []))
        files, texts, stats = {}, {}, {'visited_files': 0, 'indexed_files': 0, 'skipped_files': 0,
                                    'excluded_dirs': 0, 'bounded': False}

        def read_source(relative, explicit=False):
            relative = str(relative).replace('\\', '/')
            if relative in files:
                return texts.get(relative, '')
            path = safe_path(root, relative)
            size = path.stat().st_size
            limit = 32 * 1024 * 1024 if explicit else int(policy['max_file_bytes'])
            if size > limit:
                raise ValueError(f'文件超过扫描上限 ({limit} bytes): {relative}')
            before_stat = path.stat()
            raw = path.read_bytes()
            after_stat = path.stat()
            if (before_stat.st_size, before_stat.st_mtime_ns) != (after_stat.st_size, after_stat.st_mtime_ns):
                raise ValueError(f'扫描期间文件变化，请刷新重试: {relative}')
            raw_hash = hashlib.sha256(raw).hexdigest()
            text = ''
            if path.suffix.lower() in TEXT_EXTENSIONS and size <= int(policy['max_file_bytes']):
                text = raw.decode('utf-8-sig', errors='replace')
                texts[relative] = text
            files[relative] = {'path': relative, 'sha256': raw_hash, 'size': size,
                               'content_version': digest(canonical_text(text)) if text else raw_hash}
            return text

        # os.walk traversal is sorted and bounded; ignored bulk trees are pruned before descent.
        for directory, dirs, names in os.walk(root, followlinks=False):
            directory = Path(directory)
            depth = len(directory.relative_to(root).parts)
            kept = [d for d in sorted(dirs) if d not in excludes
                    and not d.startswith('overleaf_upload') and not is_link(directory / d)
                    and depth < int(policy['max_depth'])]
            stats['excluded_dirs'] += len(dirs) - len(kept)
            dirs[:] = kept
            for name in sorted(names):
                stats['visited_files'] += 1
                if stats['visited_files'] > int(policy['max_files']):
                    stats['bounded'] = True
                    break
                path = directory / name
                if is_link(path) or path.suffix.lower() != '.md' or path.stat().st_size > int(policy['max_file_bytes']):
                    stats['skipped_files'] += 1
                    continue
                read_source(path.relative_to(root).as_posix())
            if stats['bounded']:
                warnings.append(f'{spec["id"]}: 达到 max_files，索引为明确截断的候选')
                break

        definitions = []
        lightweight = {relative: parse_document(text) for relative, text in texts.items()}
        lightweight = {relative: meta for relative, meta in lightweight.items() if meta}
        if spec.get('mode') == 'mapped':
            definitions = [dict(n) for n in spec.get('nodes', [])]
        else:
            for relative, text in list(texts.items()):
                meta = metadata(text, relative)
                if meta:
                    definitions.append({**meta, 'entry': relative})
            if not definitions:
                # Conservative bootstrap: README roots / meaningful document folders only.
                root_id = spec['id']
                entries = [p for p in sorted(texts) if Path(p).name.lower() == 'readme.md']
                root_entry = next((p for p in entries if '/' not in p), next(iter(texts), None))
                if root_entry is None:
                    warnings.append(f'{spec["id"]}: 没有可索引 Markdown')
                    continue
                definitions.append({'id': root_id, 'entry': root_entry, 'parent': None,
                                    'kind': 'project', 'title': spec['title'], 'review': 'candidate'})
                for relative in entries[:80]:
                    if relative == root_entry:
                        continue
                    suffix = hashlib.sha256(relative.encode()).hexdigest()[:12]
                    definitions.append({'id': f'{root_id}.candidate.{suffix}', 'entry': relative,
                                        'parent': root_id, 'kind': 'module', 'review': 'candidate'})
                warnings.append(f'{spec["id"]}: 未带协议，已生成旁路候选；分类和摘要尚未审阅')

        # Markdown owns the id/title/purpose of managed folders, even when an older
        # research catalog is retained as the optional detail view.
        for entry, meta in lightweight.items():
            candidates = [other for other in lightweight if other != entry and
                          Path(other).parent in Path(entry).parent.parents]
            parent_entry = max(candidates, key=lambda p: len(Path(p).parts), default=None)
            parent_id = lightweight[parent_entry]['id'] if parent_entry else None
            existing = next((n for n in definitions if n['entry'] == entry), None)
            values = {'id': meta['id'], 'title': meta['title'], 'purpose': meta['purpose'],
                      'entry': entry, 'parent': parent_id, '_markdown_first': True}
            if existing:
                if existing['id'] != meta['id']:
                    raise ValueError(f'{entry}: Markdown 稳定 ID 与旧映射冲突，需要显式迁移')
                existing.update(values)
            else:
                definitions.append({**values, 'kind': 'project' if parent_id is None else 'module',
                                    'summary': '目录导航已接入；当前工作、结论与限制请阅读项目 Markdown。',
                                    'review': 'candidate'})

        for definition in definitions:
            node_id = definition.get('id', '')
            if not isinstance(node_id, str) or not ID.fullmatch(node_id) or node_id in nodes:
                raise ValueError(f'节点 ID 无效或重复: {node_id}')
            entry = definition['entry'].replace('\\', '/')
            body = read_source(entry, explicit=True)
            if not body:
                raise ValueError(f'节点入口必须是可读取文本: {entry}')
            evidence = []
            members = [entry, *definition.get('members', [])]
            for ref in definition.get('evidence', []):
                ref = {'path': ref, 'label': ref} if isinstance(ref, str) else dict(ref)
                relative = ref['path'].replace('\\', '/')
                read_source(relative, explicit=True)
                evidence.append({**ref, 'path': relative, 'sha256': files[relative]['sha256']})
                members.append(relative)
            for relative in members:
                read_source(relative, explicit=True)
            title_match = re.search(r'^#\s+(.+)', body, re.M)
            title = definition.get('title') or (title_match.group(1) if title_match else Path(entry).parent.name)
            flags = definition.get('flags', {})
            if not isinstance(flags, dict) or any(not isinstance(flags.get(k, []), list) for k in ('blocked', 'unverified', 'decisions')):
                raise ValueError(f'{node_id}: flags 必须是三个字符串数组')
            node = {'id': node_id, 'title': title, 'purpose': definition.get('purpose', '待审阅的文档职责候选'),
                    'kind': definition.get('kind', 'module'), 'parent': definition.get('parent'),
                    'children': [], 'related': definition.get('related', []),
                    'project_id': spec['id'], 'project_root': str(root.resolve()), 'entry': entry,
                    'source_type': spec.get('source_type', 'external'),
                    'review': definition.get('review', 'candidate'),
                    'summary': definition.get('summary', '已发现文档入口，当前结论待审阅。'),
                    'body': body, 'evidence': evidence, 'members': sorted(set(members)),
                    'constraints': definition.get('constraints', []),
                    'flags': {k: flags.get(k, []) for k in ('blocked', 'unverified', 'decisions')},
                    'stages': {k: definition.get('stages', {}).get(k, 'unknown')
                               for k in ('delivery', 'integration', 'execution', 'acceptance')}}
            # Generated summaries never become new business inputs.
            semantic = {k: v for k, v in node.items() if k not in ('body', 'evidence', 'project_root')}
            semantic['evidence'] = [{k: v for k, v in e.items() if k != 'sha256'} for e in evidence]
            semantic['sources'] = {p: files[p]['content_version'] for p in node['members']}
            node['version'] = digest(semantic)
            nodes[node_id] = node
            if definition.get('children'):
                legacy_children[node_id] = definition['children']

        stats['indexed_files'] = len(files)
        git = git_info(root)
        owned = {p for n in nodes.values() if n['project_id'] == spec['id'] for p in n['members']}
        unassigned = [p for p in sorted(texts) if p.endswith('.md') and p not in owned]
        projects.append({'id': spec['id'], 'title': spec['title'], 'root': str(root.resolve()),
                         'source_type': spec.get('source_type', 'external'), 'mode': spec.get('mode', 'protocol'),
                         'git': git, 'files': [files[p] for p in sorted(files)], 'stats': stats,
                         'unassigned_documents': unassigned,
                         'fingerprint': digest({p: f['sha256'] for p, f in sorted(files.items())}),
                         'scan_policy': {**policy, 'excluded_dirs': sorted(excludes),
                                         'explicit_references_override_bulk_exclusions': True,
                                         'markdown_metadata_scope': 'only Markdown read within this bounded detail scan; no independent full-directory discovery',
                                         'full_directory_coverage': False,
                                         'fingerprint_scope': 'indexed files, not entire repository'}})

    # Backward compatibility with handoff children.entry. Never duplicate parent truth.
    for node_id, refs in legacy_children.items():
        parent = nodes[node_id]
        for ref in refs:
            child_entry = (Path(parent['entry']).parent / ref['entry']).as_posix()
            matches = [n for n in nodes.values() if n['project_id'] == parent['project_id'] and n['entry'] == child_entry]
            if len(matches) != 1:
                raise ValueError(f'{node_id}: 子入口未匹配唯一节点 {child_entry}')
            child = matches[0]
            if child['parent'] not in (None, node_id):
                raise ValueError(f'{child["id"]}: 父关系声明冲突')
            child['parent'] = node_id

    validate_tree(nodes)
    for node in nodes.values():
        if node['parent']:
            nodes[node['parent']]['children'].append(node['id'])
    for node in nodes.values():
        node['children'].sort()
        # Capture tree moves in the version after legacy resolution.
        node['version'] = digest([node['version'], node['parent'], node['children']])
    for project in projects:
        roots = [n['id'] for n in nodes.values() if n['project_id'] == project['id'] and not n['parent']]
        if len(roots) != 1:
            raise ValueError(f'{project["id"]}: 每个项目必须恰有一个根节点，实际 {roots}')
        project['root_node'] = roots[0]
    return {'schema': 1, 'scanner_version': SCANNER_VERSION,
            'scan_id': digest({k: n['version'] for k, n in sorted(nodes.items())}),
            'scanned_at_ns': scanned_at_ns,
            'scanned_at': now(), 'nodes': nodes, 'projects': projects, 'warnings': warnings,
            'scan_policy': {'scanner_version': SCANNER_VERSION, 'execution': 'never execute source code', 'refresh': 'explicit',
                            'generated_regions': 'excluded from semantic versions', 'config': str(Path(config_path).resolve())}}


def validate_tree(nodes):
    for node in nodes.values():
        parent = node.get('parent')
        if parent and (parent not in nodes or nodes[parent]['project_id'] != node['project_id']):
            raise ValueError(f'{node["id"]}: 父节点不存在或跨项目')
        seen = {node['id']}
        while parent:
            if parent in seen:
                raise ValueError(f'主导航树存在环: {node["id"]}')
            seen.add(parent)
            parent = nodes[parent].get('parent')
        for ref in node.get('related', []):
            if not isinstance(ref, dict) or ref.get('id') not in nodes or not ref.get('relation'):
                raise ValueError(f'{node["id"]}: 无效关联节点')


def write_json(path, value):
    import tempfile
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
