# 数据说明 · 独立分支

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
      "unverified": [],
      "decisions": []
    },
    "stages": {
      "delivery": "complete",
      "integration": "not_applicable",
      "execution": "not_applicable",
      "acceptance": "pending"
    },
    "evidence": [],
    "related": [],
    "id": "sample.research.data",
    "title": "数据说明 · 独立分支",
    "kind": "module",
    "purpose": "说明示例数据的来源性质，作为局部变更传播的无关分支对照。",
    "parent": "sample.research",
    "summary": "本样例只有人工编写的教学记录，没有真实市场数据或模拟样本。"
  }
}
```

这是独立兄弟节点。实验 A 或 B 的交付不修改本节点，其摘要不应因此成为待汇总。跨链接只建立上下文关系，不自动复制主树或传播父节点失效。

不得把教学记录的数值、退出码或阶段状态当作实际性能测量。
