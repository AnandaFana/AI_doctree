<!-- doctree:node {"schema":2,"id":"sample.v2","title":"StringNote · v2 教学样例"} -->
<!-- doctree:purpose:start -->
> 本目录职责：说明虚构文本标签项目的范围、源码与文档入口，以及由人或 Agent 决定是否更新 README 的维护方式。
<!-- doctree:purpose:end -->
<!-- doctree:nav:start -->
> 上级：无（项目入口）
> 子目录：[接口与阅读说明](docs/README.md) · [文本处理入口](src/README.md) · [未执行的教学用例](tests/README.md)
> 更新约定：先更新本目录；影响范围、结论或下一步时核对上级 README。执行完成与业务/科学验收分别记录。
<!-- doctree:nav:end -->

# StringNote：五个节点的 Markdown 教学样例

这是一个虚构的纯文本标签整理项目，用来展示 DocTree v2 的目录职责、父子导航
和可选的 Git 文档核对流程。它不是已验证的业务组件。

- [src/formatter.py](src/formatter.py) 是字符串格式化入口。
- [src/rules/whitespace.py](src/rules/whitespace.py) 保存具体空白清理规则。
- [docs/contract.md](docs/contract.md) 描述当前源码约定。
- [tests/cases.json](tests/cases.json) 保存尚未执行的教学输入输出。

本样例只接入根、src、src/rules、docs、tests 五个目录。职责和正文是共享依据；
父子导航由工具生成。这个范围是教学选择，不意味着其他项目也要逐个目录接入。

## 看一次源码变化后的核对

在 DocTree 工具仓库根目录执行：

```powershell
python -X utf8 scripts/demo_v2_workflow.py
```

脚本复制本样例到系统临时目录，建立独立 Git 基线，再只改源码文本，演示
“提示叶节点核对 → 有理由地确认 README 无需改动 → 拒绝过期版本 → 按决定提示直接父节点”。
回放会保留临时目录和报告，不修改这里的文件，不运行文本处理代码，也不调用模型。

当前目录属于外层工具仓库，不是独立 Git 项目根；不要在这里直接建立维护基线。
回放记录的是固定教学决定和工具检查，不是新一次 Agent 语义审阅或业务验收。
