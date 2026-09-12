# 均值回复 · 文档核对

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
        "文档核对不替代研究结论的领域验收。"
      ],
      "decisions": []
    },
    "stages": {
      "delivery": "pending",
      "integration": "not_applicable",
      "execution": "pending",
      "acceptance": "pending"
    },
    "evidence": [],
    "related": [
      {
        "id": "sample.research.data",
        "relation": "depends_on"
      }
    ],
    "id": "sample.research.mean_reversion",
    "title": "均值回复 · 文档核对",
    "kind": "strategy",
    "purpose": "核对执行与验收的分离，以及新决策对历史待办的优先级。",
    "parent": "sample.research.strategies",
    "summary": "实验 A、B 的结论草稿均待按原始依据核对。"
  }
}
```

策略的两个实验叶节点互相独立。一项文档修复不能代替另一项；当两边都发生变化，上级摘要须覆盖两份最新版本，并保留它们尚未解决的事项。
