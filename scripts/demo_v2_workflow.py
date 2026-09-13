"""Replay fixed documentation decisions in an independent temporary Git copy.

The public fixture is never modified. This checks DocTree's maintenance workflow;
it does not run the fictional text processor, call a model, or perform a fresh
natural-language review. Every run retains its inputs, failures and small report.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from doctree import maintenance, markdown_protocol as protocol

SAMPLE = ROOT / 'examples' / 'v2'
LEAF = 'sample.v2.src.rules'
PARENT = 'sample.v2.src'
PROJECT = 'sample.v2'
REVIEWER = 'DocTree fixed-decision fixture replay; not a fresh Agent review'
IGNORED = {'.git', '.doctree', '__pycache__'}


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def fingerprints(root):
    """Read only ordinary fixture files, excluding Git and local runtime data."""
    result = {}
    for current, directories, filenames in os.walk(root, followlinks=False):
        directories[:] = sorted(name for name in directories if name not in IGNORED)
        for name in directories:
            protocol._check_ancestry(Path(current) / name)
        for name in sorted(filenames):
            if name.endswith('.pyc'):
                continue
            path = Path(current) / name
            relative = path.relative_to(root).as_posix()
            checked = protocol._safe(root, relative)
            result[relative] = hashlib.sha256(checked.read_bytes()).hexdigest()
    return result


@contextmanager
def isolated_git_environment():
    """Git variables and global hooks must not redirect the disposable project."""
    previous = {key: value for key, value in os.environ.items() if key.startswith('GIT_')}
    for key in previous:
        os.environ.pop(key, None)
    os.environ.update({'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull,
                       'GIT_TERMINAL_PROMPT': '0', 'GIT_NO_REPLACE_OBJECTS': '1'})
    try:
        yield
    finally:
        for key in list(os.environ):
            if key.startswith('GIT_'):
                os.environ.pop(key, None)
        os.environ.update(previous)


def git(project, *args):
    process = subprocess.run(
        ['git', '--no-optional-locks', '-c', 'core.fsmonitor=false',
         '-c', 'commit.gpgsign=false', '-C', str(project), *args],
        capture_output=True, text=True, encoding='utf-8', timeout=30)
    if process.returncode:
        raise RuntimeError(f'Temporary Git command failed ({args[0]}): {process.stderr.strip()}')
    return process.stdout.strip()


def run(output_root):
    protocol._check_ancestry(output_root.absolute())
    if not output_root.resolve().is_relative_to((ROOT / 'artifacts' / 'v4').resolve()):
        raise ValueError('Replay reports must stay under the tool repository artifacts/v4 directory.')
    run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8]
    artifacts = output_root.resolve() / run_id
    artifacts.mkdir(parents=True, exist_ok=False)
    sandbox = Path(tempfile.mkdtemp(prefix='doctree-v2-replay-')).resolve()
    project = sandbox / 'project'
    report = {'schema': 1, 'run_id': run_id, 'status': 'running',
              'mode': 'fixed-decision replay in independent temporary Git repository',
              'evidence_boundary': 'DocTree workflow checks only; no sample source executed; '
                                   'no model call or fresh semantic/business acceptance',
              'source_fixture': str(SAMPLE), 'temporary_project': str(project),
              'artifacts': str(artifacts), 'checks': {}}

    def require(name, passed):
        report['checks'][name] = bool(passed)
        if not passed:
            raise AssertionError(name)

    def snapshot(name):
        value = maintenance.check(project)
        if not value.get('complete'):
            raise AssertionError(f'{name}: maintenance check incomplete')
        write_json(artifacts / (name + '.json'), value)
        return {node['node_id']: node for node in value['nodes']}

    def acknowledge(node_id, token, *, parent_impact, reason, parent_reason):
        return maintenance.acknowledge(
            project, node_id=node_id, token=token, decision='no_change',
            reason=reason, reviewer=REVIEWER, parent_impact=parent_impact,
            parent_reason=parent_reason)

    try:
        if not shutil.which('git'):
            raise RuntimeError('Git is required for the isolated replay.')
        source_before = fingerprints(SAMPLE)
        shutil.copytree(SAMPLE, project, symlinks=True,
                        ignore=shutil.ignore_patterns(*IGNORED, '*.pyc'))
        require('copied_fixture_exactly', fingerprints(project) == source_before)
        readmes_before = {path: digest for path, digest in source_before.items()
                          if Path(path).name.casefold() == 'readme.md'}
        write_json(artifacts / '00-fixture-fingerprints.json', source_before)
        nodes = protocol.discover_nodes(project)
        require('five_v2_nodes', len(nodes) == 5 and {node['id'] for node in nodes} ==
                {PROJECT, PARENT, LEAF, 'sample.v2.docs', 'sample.v2.tests'})

        with isolated_git_environment():
            git(project, 'init', '-q')
            hooks = sandbox / 'empty-hooks'
            hooks.mkdir()
            for name, value in (('user.name', 'DocTree fictional replay'),
                                ('user.email', 'fixture@example.invalid'),
                                ('core.autocrlf', 'false'), ('core.fsmonitor', 'false'),
                                ('core.hooksPath', str(hooks)), ('commit.gpgsign', 'false')):
                git(project, 'config', '--local', name, value)
            require('independent_git_root', Path(git(project, 'rev-parse', '--show-toplevel')).resolve() == project)
            require('no_remote', git(project, 'remote') == '')
            git(project, 'add', '--all', '--', '.')
            git(project, 'commit', '-q', '-m', 'Initial fictional v2 fixture')
            baseline = maintenance.initialize(project)
            write_json(artifacts / '00-initialize.json', baseline)
            report['base_commit'] = baseline['base_commit']
            initial = snapshot('01-baseline')
            require('baseline_is_not_review', all(node['status'] == 'baseline' for node in initial.values()))

            rule = project / 'src' / 'rules' / 'whitespace.py'
            original = rule.read_text(encoding='utf-8')
            return_line = '    return value.strip()\n'
            if original.count(return_line) != 1:
                raise ValueError('Fixture rule changed; inspect the replay edits before running it again.')
            first_text = original.replace(return_line, '    cleaned = value.strip()\n    return cleaned\n', 1)
            rule.write_text(first_text, encoding='utf-8', newline='')
            first = snapshot('02-source-only-change')
            require('source_change_only_queues_leaf',
                    {node_id for node_id, node in first.items() if node['status'] == 'needs_review'} == {LEAF})
            require('changed_path_is_source', [change['path'] for change in first[LEAF]['changes']] ==
                    ['src/rules/whitespace.py'])
            first_token = first[LEAF]['token']
            result = acknowledge(
                LEAF, first_token, parent_impact='not_needed',
                reason='已对照本教学差异：仅把 strip() 结果存入局部变量后返回。规则 README 的职责和首尾空白说明保持准确。回放未执行函数。',
                parent_reason='调用入口与规则语义未改变，不需要父目录重新核对。')
            write_json(artifacts / '03-leaf-no-change-ack.json', result)
            first_ack = snapshot('04-leaf-only-reviewed')
            require('no_change_clears_only_leaf', first_ack[LEAF]['status'] == 'reviewed' and
                    all(first_ack[node_id]['status'] == initial[node_id]['status']
                        for node_id in initial if node_id != LEAF))
            first_record = json.loads((project / maintenance.RECORD).read_text(encoding='utf-8'))
            require('reason_and_parent_decision_recorded', first_record['reviews'][LEAF]['decision'] == 'no_change'
                    and first_record['reviews'][LEAF]['reason']
                    and first_record['reviews'][LEAF]['parent_impact'] == 'not_needed')

            before_docstring = '"""Return a label without outer whitespace."""'
            after_docstring = '"""Remove outer whitespace; preserve all spacing inside the label."""'
            if before_docstring not in first_text:
                raise ValueError('Fixture docstring changed; inspect the replay before continuing.')
            rule.write_text(first_text.replace(before_docstring, after_docstring, 1), encoding='utf-8', newline='')
            second = snapshot('05-new-source-token')
            require('new_source_invalidates_old_token', second[LEAF]['status'] == 'needs_review'
                    and second[LEAF]['token'] != first_token)
            record_before_stale = (project / maintenance.RECORD).read_bytes()
            stale = {'node_id': LEAF, 'token': first_token, 'status': 'unexpectedly_accepted'}
            try:
                acknowledge(LEAF, first_token, parent_impact='not_needed', reason='旧版本教学请求；预期被拒绝。',
                            parent_reason='此请求只用于验证旧 token 保护。')
            except ValueError as exc:
                stale.update(status='rejected', error=str(exc))
            write_json(artifacts / '06-stale-ack.json', stale)
            require('stale_token_rejected', stale['status'] == 'rejected' and
                    ('版本' in stale.get('error', '') or 'token' in stale.get('error', '').lower()))
            require('stale_ack_keeps_record', record_before_stale == (project / maintenance.RECORD).read_bytes())

            second_token = second[LEAF]['token']
            result = acknowledge(
                LEAF, second_token, parent_impact='needed',
                reason='已对照本教学差异：源码注释补充“内部空白保持原样”，与本层 README 和 strip() 表达的现有规则一致，本页无需修改。',
                parent_reason='本次澄清的是共享规则的接口说明，请直接父目录核对公开调用入口的描述是否仍一致。')
            write_json(artifacts / '07-leaf-parent-needed-ack.json', result)
            requested = snapshot('08-direct-parent-requested')
            require('needed_queues_direct_parent_only',
                    {node_id for node_id, node in requested.items() if node['status'] == 'needs_review'} == {PARENT}
                    and requested[PROJECT]['status'] == 'baseline')
            expected_inputs = [{'node_id': LEAF, 'token': second_token}]
            require('parent_request_has_current_child_token', requested[PARENT]['child_requests'] == expected_inputs)
            result = acknowledge(
                PARENT, requested[PARENT]['token'], parent_impact='not_needed',
                reason='已对照规则的当前说明、formatter.py 与 src/README.md：父层只描述委托关系，未重复字符级细节；当前入口职责仍成立，无需改写本页。',
                parent_reason='项目范围和根目录入口没有变化，本次文档核对在 src 停止，不继续提示根目录。')
            write_json(artifacts / '09-parent-no-change-ack.json', result)
            final = snapshot('10-final-check')
            record = json.loads((project / maintenance.RECORD).read_text(encoding='utf-8'))
            write_json(artifacts / '11-review-record.json', record)
            require('parent_records_child_inputs', record['reviews'][PARENT]['child_inputs'] == expected_inputs)
            require('parent_not_needed_stops_upward', final[PARENT]['status'] == 'reviewed'
                    and final[PROJECT]['status'] == 'baseline'
                    and all(node['status'] != 'needs_review' for node in final.values()))
            report['source_diff'] = git(project, 'diff', '--no-ext-diff', '--no-textconv', '--', 'src/rules/whitespace.py')

        final_files = fingerprints(project)
        require('all_five_readmes_unchanged', len(readmes_before) == 5 and
                all(final_files[path] == digest for path, digest in readmes_before.items()))
        require('public_fixture_unchanged', fingerprints(SAMPLE) == source_before)
        require('no_legacy_state_created', not (project / '.doctree' / 'state.json').exists())
        report.update(status='passed', checks_passed=sum(report['checks'].values()),
                      checks_total=len(report['checks']), sample_source_executed=False,
                      parent_child_inputs=record['reviews'][PARENT]['child_inputs'])
    except Exception as exc:
        report.update(status='failed', error_type=type(exc).__name__, error=str(exc),
                      checks_passed=sum(report['checks'].values()), checks_total=len(report['checks']))
    finally:
        write_json(artifacts / 'report.json', report)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'artifacts' / 'v4' / 'replay',
                        help='artifacts/v4 内的报告根目录；每次创建新子目录，工作副本始终位于系统临时目录。')
    options = parser.parse_args()
    result = run(options.output)
    print(json.dumps({key: result.get(key) for key in
                      ('status', 'checks_passed', 'checks_total', 'temporary_project', 'artifacts', 'error')},
                     ensure_ascii=False, indent=2))
    raise SystemExit(0 if result['status'] == 'passed' else 1)
