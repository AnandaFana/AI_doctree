# 实验 B · 历史不自动变待办

```yaml
{
  "project_node": {
    "schema": 1,
    "review": "candidate",
    "constraints": [
      "此项目为协议教学样例，不代表真实交易、模型性能或科学实验结果。",
      "执行完成不等于业务或科学验收；验收需要独立判断和可追溯依据。"
    ],
    "flags": {
      "blocked": [],
      "unverified": [
        "新的研究结论仍需独立验收；文档核对不构成新的采样证据。"
      ],
      "decisions": []
    },
    "stages": {
      "delivery": "pending",
      "integration": "not_applicable",
      "execution": "pending",
      "acceptance": "pending"
    },
    "evidence": [
      {
        "path": "research/mean_reversion/experiment_b/source_record.md",
        "label": "带日期的当前决策和历史计划"
      },
      {
        "path": "research/mean_reversion/experiment_b/conclusion.md",
        "label": "待核对范围草稿"
      }
    ],
    "related": [],
    "id": "sample.research.experiment_b",
    "title": "实验 B · 历史不自动变待办",
    "kind": "experiment",
    "purpose": "核对当前范围决策与旧实验计划冲突时的解释和保留方式。",
    "parent": "sample.research.mean_reversion",
    "summary": "当前范围关闭扩样；历史计划保留，结论草稿待核对。"
  }
}
```

任务 B：保留旧计划文本，修正当前任务的范围说明。这个独立叶节点用于连续两叶变化实验，也用于检验历史文件出现“下一步”是否会错误重开已关闭任务。
