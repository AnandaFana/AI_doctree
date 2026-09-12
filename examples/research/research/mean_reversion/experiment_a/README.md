# 实验 A · 执行不等于验收

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
        "示例研究结论尚未验收。"
      ],
      "decisions": [
        "是否接受研究结论须由领域负责人审阅。"
      ]
    },
    "stages": {
      "delivery": "complete",
      "integration": "not_applicable",
      "execution": "complete",
      "acceptance": "pending"
    },
    "evidence": [
      {
        "path": "research/mean_reversion/experiment_a/source_record.md",
        "label": "原始示例记录与验收条件"
      },
      {
        "path": "research/mean_reversion/experiment_a/conclusion.md",
        "label": "待核对结论草稿"
      }
    ],
    "related": [],
    "id": "sample.research.experiment_a",
    "title": "实验 A · 执行不等于验收",
    "kind": "experiment",
    "purpose": "对照示例运行记录，修正文档把执行成功升级为研究通过的表述。",
    "parent": "sample.research.mean_reversion",
    "summary": "示例记录显示执行完成、验收待定；结论草稿有待核对。"
  }
}
```

任务 A：阅读原始示例记录和结论草稿，逐项核对“完成”的对象，修改有依据的文档措辞；将真实阅读范围、修改、未解决项写成结构化交付。文档审阅完成后仍不能把示例科学验收改成通过。
