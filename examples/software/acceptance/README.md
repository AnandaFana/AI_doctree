# 业务验收

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
        "业务效果缺少验收依据。"
      ],
      "decisions": [
        "负责人确认验收标准和适用范围。"
      ]
    },
    "stages": {
      "delivery": "pending",
      "integration": "not_applicable",
      "execution": "not_applicable",
      "acceptance": "pending"
    },
    "evidence": [],
    "related": [],
    "id": "sample.software.acceptance",
    "title": "业务验收",
    "kind": "decision",
    "purpose": "保留独立的验收条件与负责人决策入口。",
    "parent": "sample.software",
    "summary": "验收条件仍待负责人确认；不因其他节点完成而升级。"
  }
}
```

验收记录应回答：针对哪个版本、哪些目标、什么证据、由谁在何时接受或拒绝。第一版不引入审批系统；把缺失条件以结构化标记保留下来。

本节点的 pending 是样例设计，并不意味着本次 AI DocTree 交付必然失败。
