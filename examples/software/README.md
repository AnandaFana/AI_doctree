# 交付样例 · 四个完成阶段

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
        "业务验收尚未完成。"
      ],
      "decisions": [
        "需要负责人确认验收标准及结果。"
      ]
    },
    "stages": {
      "delivery": "pending",
      "integration": "not_applicable",
      "execution": "pending",
      "acceptance": "pending"
    },
    "evidence": [],
    "related": [],
    "id": "sample.software",
    "title": "交付样例 · 四个完成阶段",
    "kind": "project",
    "purpose": "展示文件交付、代码集成、运行完成和业务验收分别回答的问题。",
    "parent": null,
    "summary": "样例交付包已经形成，集成和执行各有记录，业务验收仍待决定。"
  }
}
```

这是第二个独立项目，所有阶段事实均为教学夹具。不能从子模块的 delivery 或 execution 为 complete 推导根项目 acceptance 为 complete。
