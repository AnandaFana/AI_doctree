"""Replay a documented Codex review. Default creates an isolated sandbox.

This script replays fixed, previously reviewed document decisions; it does not call
an AI or claim that a future replay is a fresh natural-language review.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from doctree.cli import Application
from doctree.governance import StateStore, ConflictError
from doctree.scanner import file_hash, write_json, now

A = 'sample.research.experiment_a'
B = 'sample.research.experiment_b'
STRATEGY = 'sample.research.mean_reversion'
PATHS = {A: 'research/mean_reversion/experiment_a', B: 'research/mean_reversion/experiment_b'}
FINAL = {
    A: '''# 已核对结论 A

2026-09-12，Codex 对照 [原始示例记录](source_record.md) 完成文档一致性审阅。

记录只支持“示例任务执行结束、预期文件齐全”。退出码为 0 不能推出研究结论已通过，
也不能推出适合实际应用。原稿中这两个推断均缺少依据，已撤回。

任务交付与文档核对执行已完成；代码集成不适用。研究验收仍为 **pending**，
需要独立样本、误差范围和领域负责人审阅；是否接受结论仍是待决策事项。

本轮只阅读和修改教学文档，没有运行研究程序、模拟交易或新增科学证据。
''',
    B: '''# 已核对结论 B

2026-09-12，Codex 对照 [原始示例记录](source_record.md) 完成范围一致性审阅。

当前行动是核对并整理现有文档，扩样关闭。依据是当前决定明确声明取代
2026-09-01 计划的近期行动，且两者作用于同一工作范围；不是仅凭日期较新作判断。

历史计划保留原样，用来说明决策演变；它不自动恢复为当前任务。新的采样需要另行
明确决定。本次文档修复已交付，研究结论仍需独立验收，验收状态保持 **pending**。

本轮没有开展采样或独立确认。审阅确认的是文档解释与来源一致。
''',
}
SUMMARIES = {
    A: '已核对实验 A：退出码与文件齐全仅支持示例执行结束；撤回研究已通过及可直接应用的推断。文档交付完成，研究验收仍 pending，待领域负责人决定。',
    B: '已核对实验 B：当前停止扩样决定明确替代旧计划的近期行动；历史保留，不恢复采样任务。本轮仅修正文档，没有新增采样或独立确认证据。',
    STRATEGY: '两份最新文档均已核对：A 撤回执行成功推出研究通过的表述；B 按明确替代关系保留历史并维持扩样关闭。两项文档交付完成，研究验收仍 pending。',
    'sample.research.strategies': '均值回复分支的两项文档核对已交付并逐层审阅；执行与验收的边界、停止扩样决定均保留，仍需独立领域验收。',
    'sample.research': '研究样例完成两项文档一致性修正：区分执行与研究验收，并按当前决定解释历史计划。可以沿策略树追溯 A、B 的交付和原始示例记录；数据分支未变，研究验收未通过。',
}


def run(app, artifacts, research_root, live=False):
    artifacts.mkdir(parents=True, exist_ok=True)
    initial = app.refresh()
    app.store.mark_viewed()
    contexts = {node_id: app.store.export_context(node_id) for node_id in (STRATEGY, A, B)}
    for node_id, context in contexts.items():
        write_json(artifacts / f'context-{node_id}.json', context)
    before_versions = {k: v['version'] for k, v in initial['nodes'].items()}
    report = {'at': now(), 'mode': 'live Codex document review' if live else 'fixed-decision replay in isolated sandbox',
              'review_scope': 'document consistency only; no source code or scientific experiment executed',
              'checks': {}, 'deliveries': [], 'summaries': [], 'source_changes': []}
    reviewer = 'Codex / 2026-09-12 文档审阅' if live else 'replay of Codex 2026-09-12 document review (not fresh AI review)'
    stale = None
    for node_id in (A, B):
        conclusion = research_root / PATHS[node_id] / 'conclusion.md'
        old_text = conclusion.read_text(encoding='utf-8')
        (artifacts / f'before-{node_id}.md').write_text(old_text, encoding='utf-8')
        before_hash = file_hash(conclusion)
        conclusion.write_text(FINAL[node_id], encoding='utf-8')
        current = app.refresh()
        report['source_changes'].append({'node_id': node_id, 'file': str(conclusion),
                                        'before_sha256': before_hash, 'after_sha256': file_hash(conclusion)})
        pending = {n['id'] for n in app.store.pending()['nodes']}
        expected_ancestors = {STRATEGY, 'sample.research.strategies', 'sample.research'}
        if node_id == A:
            report['checks']['only_A_ancestors_invalidated'] = pending == expected_ancestors
        node = current['nodes'][node_id]
        evidence = [{'path': f'{PATHS[node_id]}/{name}', 'label': label}
                    for name, label in [('source_record.md', '原始示例依据'), ('conclusion.md', '本轮文档修正')]]
        delivery = {'schema': 1, 'task_id': f'doc-review-20260912-{node_id.rsplit("_",1)[-1]}',
                    'node_id': node_id, 'based_on_version': node['version'],
                    'changes': [SUMMARIES[node_id]], 'evidence': evidence,
                    'validation': {'method': '逐句对照原始示例记录与改动文档', 'result': 'document_consistency_checked',
                                   'context_input_version': contexts[node_id]['node']['version'],
                                   'reconciliation': 'Only the intended conclusion.md changed; refreshed source version after inspecting before/after content.',
                                   'reviewer': reviewer},
                    'unresolved': ['研究结论仍需独立领域验收；本轮没有增加科学证据。'],
                    'summary_candidate': SUMMARIES[node_id],
                    'stages': {'delivery': 'complete', 'integration': 'not_applicable',
                               'execution': 'complete', 'acceptance': 'pending'}}
        write_json(artifacts / f'delivery-{node_id}.json', delivery)
        result = app.store.import_delivery(delivery)
        report['deliveries'].append(result)
        report['checks'][f'duplicate_{node_id}'] = app.store.import_delivery(delivery)['status'] == 'duplicate'
        reopened = StateStore(app.state_dir / 'state.json')
        report['checks'][f'restart_preserves_{node_id}'] = reopened.pending() == app.store.pending()
        proposal = reopened.prepare_summary(node_id)
        proposal.update(summary=SUMMARIES[node_id], review='reviewed', reviewer=reviewer)
        write_json(artifacts / f'summary-{node_id}.json', proposal)
        report['summaries'].append(reopened.commit_summary(proposal))
        if node_id == A:
            stale = reopened.prepare_summary(STRATEGY)
            stale.update(summary='过期候选：这里只覆盖了 A 的审阅，B 尚未修正。', review='reviewed', reviewer=reviewer)
            write_json(artifacts / 'stale-parent-proposal.json', stale)

    try:
        app.store.commit_summary(stale)
        report['checks']['stale_parent_rejected'] = False
    except ConflictError as exc:
        report['checks']['stale_parent_rejected'] = True
        report['stale_conflict'] = str(exc)
    write_json(artifacts / 'pending-before-rollup.json', app.store.pending())
    for node_id in (STRATEGY, 'sample.research.strategies', 'sample.research'):
        proposal = app.store.prepare_summary(node_id)
        proposal.update(summary=SUMMARIES[node_id], review='reviewed', reviewer=reviewer)
        write_json(artifacts / f'summary-{node_id}.json', proposal)
        report['summaries'].append(app.store.commit_summary(proposal))
    final = app.store.get_state()
    strategy_inputs = final['nodes'][STRATEGY].get('summary_inputs')
    report['checks']['both_leaves_in_parent_inputs'] = set((strategy_inputs or {}).get('input_versions', {})) == {A, B}
    report['checks']['no_pending_after_review'] = app.store.pending()['count'] == 0
    report['checks']['unrelated_sources_unchanged'] = all(n['version'] == before_versions[node_id]
        for node_id, n in final['nodes'].items() if node_id not in (A, B))
    report['checks']['unrelated_branches_not_marked_changed'] = all(not n['changed']
        for node_id, n in final['nodes'].items() if node_id not in (A, B, STRATEGY, 'sample.research.strategies', 'sample.research'))
    report['checks']['acceptance_not_promoted'] = all(final['nodes'][i]['stages']['acceptance'] == 'pending' for i in (A, B, 'sample.research'))
    app.refresh()
    report['checks']['repeat_scan_keeps_review_and_queue'] = app.store.pending()['count'] == 0 and app.store.get_state()['nodes']['sample.research']['summary'] == SUMMARIES['sample.research']
    write_json(artifacts / 'context-after.json', app.store.export_context(STRATEGY))
    write_json(artifacts / 'state-after.json', final)
    write_json(artifacts / 'report.json', report)
    if not all(report['checks'].values()):
        raise AssertionError(report['checks'])
    print(json.dumps({'artifacts': str(artifacts), 'checks': report['checks']}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', help='apply this session review to the local teaching examples')
    args = parser.parse_args()
    if args.live:
        run(Application(), ROOT / 'artifacts' / 'demonstration', ROOT / 'examples' / 'research', live=True)
    else:
        sandbox = Path(tempfile.mkdtemp(prefix='doctree-replay-'))
        shutil.copytree(ROOT / 'examples', sandbox / 'examples')
        config = json.loads((ROOT / 'config' / 'projects.json').read_text(encoding='utf-8'))
        config['projects'] = [p for p in config['projects'] if p['source_type'] == 'sample']
        write_json(sandbox / 'config' / 'projects.json', config)
        # Create a changed initial draft even when checked-in examples already contain the reviewed result.
        for node_id, folder in PATHS.items():
            (sandbox / 'examples' / 'research' / folder / 'conclusion.md').write_text(
                '# 回放初始草稿\n\n待按 source_record.md 核对；此文本仅用于重放变化检测。\n', encoding='utf-8')
        run(Application(sandbox / 'config' / 'projects.json', sandbox / '.doctree'),
            ROOT / 'artifacts' / 'replay', sandbox / 'examples' / 'research')
