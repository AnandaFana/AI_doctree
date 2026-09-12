# DocTree · 给 Agent 和人类共用的目录说明

DocTree 将目录职责、父子导航和更新约定保存在项目自己的 Markdown 中。Agent 直接沿文件阅读，人类通过可折叠的目录树浏览同一份内容；文件保持原位，治理范围由用户按实际用途选择。

当前版本 **0.3.0**：文件树作为默认首页，支持展开层数、搜索、缩放和目录说明阅读。需要证据、交接与审阅历史时，可进入详细治理页。变更见 [版本说明](CHANGELOG.md)，格式见 [Markdown 节点协议 v2](docs/MARKDOWN_PROTOCOL_V2.md)。

## 先试用页面

需要 Python 3.10+。克隆后从仓库根目录运行：

```powershell
git clone https://github.com/AnandaFana/AI_doctree.git
cd AI_doctree
python -m pip install -r requirements.txt
python -X utf8 -m doctree serve
```

打开 [目录树](http://127.0.0.1:8765/) 或 [详细治理页](http://127.0.0.1:8765/details)。默认载入两个随仓库提供的教学项目，不需要任何个人研究仓库。Windows 也可运行 `.\start.ps1`，另一个终端运行 `.\stop.ps1` 停止服务；启动和停止都支持 `-Port 8766`。

目录树占据页面主体，默认展开第一层，可以切换 1、2、3 层，或手动展开、平移和搜索。详细页右上方和左侧都有明显的返回入口。**页面展开层数只影响显示，不会创建 README。**

开发工具的详细治理功能需要 PyYAML；下面的便携模块只依赖 Python 标准库，无需 Node、数据库、模型服务或 PyYAML。

## 把轻量模块放进自己的项目

从 DocTree 仓库根目录安装到一个已有项目（将 `..\my-project` 替换为实际路径）：

```powershell
python -X utf8 -m doctree.portable --root ..\my-project install
cd ..\my-project
python -X utf8 .doctree/manage.py serve
```

打开 [便携目录树](http://127.0.0.1:8768/)。`serve --port 8769` 可更换端口，`Ctrl+C` 停止服务。便携包提供树图和 Markdown 阅读；完整的详细治理页由上面的开发工具服务提供。

```text
my-project/
├── README.md          共享：职责、父子导航、更新约定与原有正文
├── src/
│   └── README.md      按选定范围接入的子目录说明
├── AGENTS.md          共享：阅读和更新指引，保留原有约定
├── .gitignore         忽略本地 .doctree/ 管理目录
└── .doctree/
    ├── manage.py      便携命令入口
    ├── AGENT_GUIDE.md 工具操作说明
    ├── lib/           Python 标准库实现
    ├── web/           HTML / CSS / JavaScript / SVG
    └── backups/       写入前原文与恢复记录
```

安装器只维护自己声明的工具文件，以及 AGENTS / `.gitignore` 中带标记的区块。共享知识保存在项目 Markdown 中；克隆项目时没带 `.doctree/`，重新安装工具即可。**Git 忽略不意味着可以随意删除**：已有治理历史 `state.json` 和原文备份无法靠扫描重建，需要另行保留。

## 选择 README 的治理深度

先在目标项目根目录查看分布，再选择合适范围：

```powershell
# 只读摘要：各层目录数量、已有说明和待补充情况
python -X utf8 .doctree/manage.py coverage --summary

# 检查根目录及直接子目录；未覆盖时返回非零退出码
python -X utf8 .doctree/manage.py coverage --depth 1 --check

# 预览接入差异，不写文件
python -X utf8 .doctree/manage.py cover --depth 1

# 确认范围和差异后应用，保留原文与备份
python -X utf8 .doctree/manage.py cover --depth 1 --apply
```

根目录为第 0 层。Agent 可以根据目录数量和用途建议深度；用户已经明确范围时直接按范围执行，范围不明确时再讨论。缓存、批次和历史产物不应一律要求逐个建立 README。需要完整范围时显式使用 `--all`；未指定范围的 `cover` 会拒绝批量接入。

可将 [范围配置示例](config/coverage-policy.example.json) 放在目标项目的共享位置，通过 `coverage --policy 文件.json` / `cover --policy 文件.json` 复用。已有 README 正文和自定义节点文档会保留。新增模板只记录可核对的位置与约定，实际职责和结论仍由人或 Agent 阅读来源后维护。

## 日常阅读和同步

从目标项目根目录运行：

```powershell
python -X utf8 .doctree/manage.py context --directory .
python -X utf8 .doctree/manage.py sync --check
python -X utf8 .doctree/manage.py sync

# 逐个接入：--check 预览，去掉 --check 应用
python -X utf8 .doctree/manage.py annotate --directory . --purpose "项目目标、职责与下级入口" --check
```

`sync --check` 有待写入差异时退出码为 1；`sync` 应用已有节点的父子导航，不批量创建普通目录的 README。更精细的接入可参考 [选择文件示例](config/selections.example.json)，从工具仓库执行 `python -m doctree sync <项目路径> --selections <选择文件> --project-id my-project` 预览，加 `--apply` 应用。

Agent 先读根 README，再沿链接阅读工作目录；完成工作后更新本目录，影响上级范围、结论或下一步时再核对父节点。工具负责结构和链接，`sync` 不自动总结正文、不判断业务或科学验收。当前没有持续文件监听、跨机器审阅通知或模型自动摘要。

## 本地项目与 Git 分开

[config/projects.json](config/projects.json) 只包含可分享的教学样例。需要管理自己的项目时，将它复制为 `config/projects.local.json`，在本地副本中配置项目入口。启动时优先读取本地配置；显式 `--config` 优先级最高，例如：

```powershell
python -X utf8 -m doctree --config config/projects.json --state-dir .doctree/sample-view serve --port 8766
```

项目路径相对于配置目录的上一级，也可使用本机绝对路径。使用另一份配置时配合独立的 `--state-dir`，避免混用治理历史。

`local-projects/` 用于存放不提交的自有项目副本；`config/*.local.json`、`.doctree/`、`artifacts/` 和本地试点材料均被忽略。若将自有项目放在其他目录，**复制前先将该目录加入 `.gitignore`**。忽略规则不会撤回已进入 Git 历史的内容；发布前检查本轮提交和历史，方法见 [发布约定](docs/PUBLISHING.md)。

## 开发与验证

```powershell
python -X utf8 -m unittest discover -s tests -v
python -X utf8 scripts/demo_workflow.py
```

演示脚本默认在临时副本中回放教学交接，不覆盖当前界面的历史。实现入口包括 [Markdown 注入](doctree/markdown_protocol.py)、[目录树与上下文](doctree/foldertree.py)、[治理范围](doctree/coverage.py)、[便携安装](doctree/portable.py)。

[当前协议](docs/MARKDOWN_PROTOCOL_V2.md) · [v1 详细治理协议](docs/PROTOCOL.md) · [方法演进记录](docs/METHODOLOGY.md) · [版本说明](CHANGELOG.md)
