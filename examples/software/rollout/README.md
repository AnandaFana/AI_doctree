# 运行记录

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
        "没有业务质量和实际用户结果的验收。"
      ],
      "decisions": []
    },
    "stages": {
      "delivery": "complete",
      "integration": "complete",
      "execution": "complete",
      "acceptance": "pending"
    },
    "evidence": [
      {
        "path": "rollout/evidence.md",
        "label": "运行与验收示例依据"
      }
    ],
    "related": [],
    "id": "sample.software.rollout",
    "title": "运行记录",
    "kind": "operation",
    "purpose": "说明代码已集成且示例执行完成，但验收仍不能自动通过。",
    "parent": "sample.software",
    "summary": "示例代码集成和执行为 complete；业务验收保持 pending。"
  }
}
```

退出码与输出文件足够说明某次运行结束。运行结果是否满足用户的目标，仍由专门的验收依据决定。
