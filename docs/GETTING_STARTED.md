# 首次安装与 Agent 接入

完整流程是：**获取工具 → 安装到目标项目 → Agent 阅读并建立说明 → 检查结构和内容 → 打开页面**。`install` 只部署工具，`serve` 只打开页面；它们不会替你理解项目或批量编写业务 README。

## 1. 在新电脑获取工具

准备 Git 和 Python 3.10+，确认 `git --version`、`python --version` 可用。本文以 Windows PowerShell 为例；macOS / Linux 可使用 `python3` 并将路径分隔符换为 `/`。

在工具存放目录执行：

```powershell
git clone https://github.com/AnandaFana/AI_doctree.git
cd AI_doctree
```

仓库为私有时，需要先具有该仓库的访问权限并完成 GitHub 登录。已经克隆过的工具仓库可在工作区干净时运行 `git pull --ff-only` 更新。本流程需要 0.3.1 或更新版本。

轻量接入全程只使用 Python 标准库，**不需要 `pip install`**。若还想运行本工具的两个教学样例和详细治理页，再安装 `requirements.txt` 并运行 `python -m doctree serve`；这是可选体验。

## 2. 安装到要管理的项目

目标项目应已经存在，工具仓库与目标项目建议分开放置。以下示例假定它们是同级目录；将 `..\my-project` 替换为实际路径，路径含空格时保留引号。

从 **AI_doctree 工具仓库根目录**执行：

```powershell
python -X utf8 -m doctree.portable --root "..\my-project" install
cd "..\my-project"
```

安装会创建 `.doctree/manage.py`、页面和操作指引，并合并根 `AGENTS.md` / `.gitignore` 中的工具区块。已有项目正文和协作规则保留。此时项目可能仍没有任何 README 节点，这是正常的；下一步才建立管理内容。

安装结果会给出 `.doctree/ONBOARDING.md` 和下一步提示。即使之后离开工具仓库，Agent 也能从目标项目内读到完整首次接入指令。

## 3. 在目标项目中交给 Agent 执行

把 Codex 或其他能读写本地文件的 Agent 的工作目录设为 **my-project**，发送下面这段指令：

```text
请为当前项目建立 DocTree 目录说明体系，执行 .doctree/ONBOARDING.md 中的首次接入流程，并遵守本项目已有的协作约定。

治理范围：先查看目录层数和数量，结合项目用途向我建议范围；如果当前对话已经确定范围，就沿用，不要重复确认。

请阅读实际来源后填写各节点的职责和必要正文，通过工具预览、注入节点及父子导航，保留原文和历史。不要停在安装工具、生成空模板或打开页面。

最后分别报告结构检查和内容核对结果，列出已治理范围、未覆盖范围及待确认事项。不要修改业务源码、运行项目实验，或替我提交、推送目标项目。
```

如果范围已经明确，把第二段改为例如“治理范围：项目根及直接子目录，深度 1”；只需几个目录时写“项目根、src、docs，其他目录不新建说明”。也可以明确写“授权你根据目录用途决定合适范围并记录取舍”，由 Agent 在这项授权内决定。展示深度与这些写入范围互相独立。

详细指令的仓库原文见 [Agent 首次接入指令](AGENT_ONBOARDING.md)。对已经安装了 0.3.0 的项目，从新版工具仓库重新执行第 2 步即可升级并补上该文件，安装器保留原有历史和备份。

## 4. Agent 应交付什么

| 交付 | 检查方式 |
|---|---|
| 明确的治理范围 | 按深度选择时保存共享 `doctree-policy.json`；按目录名单选择时记录实际名单与例外 |
| 有实际内容的目录入口 | 已选目录有节点，职责引用真实文件；新增正文说明入口、限制和维护方式，不只留下待补模板 |
| 可阅读的父子关系 | 根与子节点能沿相对链接互相定位，已有稳定 ID 和自定义入口保留 |
| 可核对的变更 | 保留原正文、工具备份及变更记录；未知状态写明未知，不推断任务已经验收 |
| 结构与内容两份结论 | `coverage` / `sync` 检查结构，Agent 根据实际来源核对内容 |

Agent 可以使用 `sync --selections 文件.json --check` 预览自己编写的职责与新文档正文，去掉 `--check` 应用；这条便携命令不依赖 PyYAML。`cover` 也能按深度建立结构模板，但模板仍需 Agent 补充真实内容，不能单凭覆盖率宣布整理完成。

若采用按深度治理，下面的命令使用 Agent 已记录的范围：

```powershell
python -X utf8 .doctree/manage.py coverage --policy doctree-policy.json --check
python -X utf8 .doctree/manage.py sync --check
python -X utf8 .doctree/manage.py context --directory .
```

若只选择少数目录，按名单核对 `coverage` 报告中相应行和 `context`；不要用整个深度的覆盖率要求未选同层目录也有 README。根节点未接入时，应明确是局部子树还是遗漏了连接入口，不擅自扩大范围。`sync --check` 为 0 只表示现有导航无差异，空项目也可能返回 0；需要结合节点清单和内容检查。

树图和 `context` 受展示深度、折叠规则和节点数量限制。深层或历史节点已经接入却无法导出上下文时，先核对具体限制，再直接检查节点 Markdown 与相对父子链接，并在报告中说明显示范围。

## 5. 打开页面并开始日常使用

在 **目标项目根目录**执行：

```powershell
python -X utf8 .doctree/manage.py serve
```

打开 [本地目录树](http://127.0.0.1:8768/)，点击根和子目录检查说明。该命令持续占用终端；结束时按 `Ctrl+C`，或另开终端执行其他命令。端口冲突时使用 `serve --port 8769`，页面地址也改为 8769。

后续在同一项目里工作，可给 Agent 一句简短指令：

```text
请遵守项目 AGENTS.md 和各级 README 的 DocTree 约定：先阅读本次工作目录及上级说明，完成工作后按实际影响更新相关说明，并检查父子导航。新目录按已经确定的治理范围处理，不扩大范围，不将执行成功自动写成业务验收。
```

项目自己的 README / DOCTREE.md、AGENTS.md 和共享范围配置可随项目 Git 流转；`.doctree/` 留在本机。需要提交项目文档时按项目本身的授权和发布规则操作。换电脑克隆项目后，重装轻量工具，再检查现有节点即可，无需把已经整理好的说明重新生成一遍。
