# 交付包

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
      "integration": "pending",
      "execution": "not_applicable",
      "acceptance": "pending"
    },
    "evidence": [
      {
        "path": "package/evidence.md",
        "label": "交付与集成示例依据"
      }
    ],
    "related": [],
    "id": "sample.software.package",
    "title": "交付包",
    "kind": "module",
    "purpose": "说明文件已经交付但尚未并入目标代码线的情形。",
    "parent": "sample.software",
    "summary": "示例交付包已形成，目标代码线集成仍 pending。"
  }
}
```

“文件可以阅读”回答交付问题；“目标代码线已包含该改动”回答集成问题。二者分别维护。
