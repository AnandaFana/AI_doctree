# Markdown 目录节点协议 v2

本协议是 DocTree 当前的轻量目录路线。**项目 Markdown 是共享依据，目录树是它与实际目录的可重建视图。** Agent 直接阅读这些文件，人类通过树图查看同一份职责和导航。关闭网页、移动项目或没有携带 `.doctree/`，不会让已写入的项目知识消失。

本协议与 [v1 详细治理协议](PROTOCOL.md) 并存。v1 的语义映射、证据、交付和审阅历史仍保留在详细页与原状态文件中；v2 不把这些历史压缩成一个新 README，也不重新解释已有科学或业务验收。

## 1. 目录与节点的粒度

默认树图按实际文件夹展示，并默认展开项目的第一层子目录。目录说明的治理范围由用户选择：可以逐个接入，也可按深度覆盖一段目录树，或明确选择完整覆盖。Agent 应先查看各层目录数量和用途，再建议范围；不能一律认为每个文件夹都必须生成 README，也不能让已选范围内的新目录悄悄漏掉。

**治理深度与展示深度分别设置。** 治理深度决定哪些目录需要评估或补齐说明，根目录为 0，直接子目录为 1；页面展开层数只控制画布，不创建或更新任何文件。普通目录可以展示而没有节点声明；页面的“尚无 README”是中性提示，不等于任务失败。

先用 `coverage` 查看按层数量和范围外目录，范围明确后再生成 `cover` 计划。`cover` 必须显式选择深度或完整范围，默认只预览，`--apply` 才写入。已有 README 正文保留，已有自定义节点文档沿用，不创建第二份相互竞争的节点入口。新增说明先提供可核对的位置与约定；具体职责和结论仍需人或 Agent 阅读来源后维护。

覆盖检查独立于网页的深度与批量产物折叠。`scope_complete` 表示所选范围已完整检查，`scope_covered` 还要求这些目录具有节点说明；`project_complete` 还要求没有延后检查的目录。预算或读取错误单独报告，不能用浅层检查冒充全仓完成。未选范围是待评估，不是错误。

可用 `--policy` JSON 保存可复用范围：`{"schema":1,"max_depth":1,"exclude_dirs":[]}`。希望跨人或跨 Agent 共享此约定时，将它与项目 Markdown 一起保存到 Git 可见位置；`.doctree/` 中不应保存唯一的范围约定。同步仍发现现有节点，范围外已接入的深层节点不会因为本次范围较浅而失去导航。

每个目录最多有一个 v2 节点文档，推荐 `README.md`；不适合修改原 README 时可用 `DOCTREE.md`。扩展接入选择允许其他 `.md` 文件名，发现器会检查 Markdown 的开头。项目根推荐保持 `README.md` 或 `DOCTREE.md`，便携工具通过这两个入口获取共享根 ID 与标题。

节点的父亲是物理路径中最近的已接入祖先。中间存在无节点声明的目录时，Markdown 链接可直接连接更上层节点；树图仍保留中间物理文件夹。没有已接入祖先的节点暂时没有协议父节点，应由接入者核对是否需要补齐根入口。

## 2. 文件格式与维护责任

完整头部放在文档开头，按身份、职责、导航的顺序排列：

```markdown
<!-- doctree:node {"schema":2,"id":"project.experiments","title":"实验实现"} -->
<!-- doctree:purpose:start -->
> 本目录职责：维护实验实现、运行入口与校验说明。
<!-- doctree:purpose:end -->
<!-- doctree:nav:start -->
> 上级：[项目总览](../README.md)
> 子目录：[某实验](example/README.md)
> 更新约定：先更新本目录；影响范围、结论或下一步时核对上级 README。执行完成与业务/科学验收分别记录。
<!-- doctree:nav:end -->

# 原来的文档标题

原来的文档正文完整保留在这里。
```

| 部分 | 含义和维护方式 |
|---|---|
| `schema` | v2 节点格式版本，固定为整数 2；与扫描算法版本分开 |
| `id` | 稳定节点身份，ASCII 字母、数字和 `_ . : -`；首字符为字母或数字，最长 160 字符 |
| `title` | 显示名称，由人或 Agent 明确维护 |
| `doctree:purpose` | 人工职责说明；工具仅在明确传入新 purpose 时更新 |
| `doctree:nav` | 工具维护的父子链接与共同更新约定；重新同步可重建 |
| 头部之后的原正文 | 人或 Agent 按实际来源维护；导航工具保留原始字节内容 |

`parent` 和 `children` 不写入身份 JSON，防止同一关系存在两份手工配置。显示链接相对于当前文档所在目录，路径中的空格或特殊字符进行 URL 编码。

已接入节点改名或移动目录时保留 `id`，再同步相对链接。同步不得自动更换已有 ID。新节点可以由项目 ID 和目录路径生成默认 ID，但它一旦进入 Markdown，就成为需要保留的身份；复杂或长路径建议显式指定短 ID。

解析器只识别文档开头的完整 v2 头部。正文里的协议示例不创建节点；缺失、错位、重复或交错的头部标记明确报错，不吞掉后续正文。`doctree:` 头部命名空间保留给节点协议；安装器的 AGENTS 辅助区块另用 `doctree-agent:` 标记。

## 3. 接入、预览与写入

接入配置是一个 JSON 数组。它只用于明确本次选择，应用后身份与职责已经在各节点 Markdown 中；日常同步无需依赖这份选择文件。

```json
[
  {"directory":".","entry":"README.md","id":"project","title":"项目总览","purpose":"说明目标、范围、当前约定和下级入口。"},
  {"directory":"docs","entry":"README.md","id":"project.docs","title":"项目说明","purpose":"维护使用方式、设计说明和贡献约定。"}
]
```

目录必须已存在；缺失的节点 Markdown 可以创建，已存在的正文保留。首次注入增加头部，后续同步只维护导航；只有选择项明确提供 `title` 或 `purpose` 时才调整对应人工字段。

从工具工作区运行：

```powershell
# 默认只输出计划，不写来源 README
python -X utf8 -m doctree sync ..\my-project --project-id project --selections .\config\selections.example.json --output .\artifacts\project-plan.json

# 根据当前输入重新生成并应用计划
python -X utf8 -m doctree sync ..\my-project --project-id project --selections .\config\selections.example.json --apply

# 日常只同步已经接入的节点
python -X utf8 -m doctree sync ..\my-project --project-id project --apply

# 安装可独立运行的工具目录；此入口本身只需标准库
python -X utf8 -m doctree.portable --root ..\my-project install
```

工具工作区 CLI 的 `--apply` 会重新读取当前来源并生成计划，不直接执行 `--output` 导出的旧计划文件。需要严格保存某次计划并稍后按原输入版本应用的集成，可使用下面的 Python API；`apply_plan` 接受已保存并再次读取的 JSON 计划。

```python
from doctree.markdown_protocol import plan_sync, apply_plan

plan = plan_sync(root, selections, project_id="project")
# 在此检查或保存 plan 的 changes、inputs 与原文差异。
report = apply_plan(plan)
```

`plan_sync(root, [], project_id)` 不创建新节点，只发现已有节点并重建导航。正文变化本身不会制造导航差异，也不会让 `sync` 自动总结或接受结论。

## 4. 版本、并发与恢复

扫描与计划生成只读。应用前核对已接入文件集合及每份输入的 SHA-256；任一输入过期时拒绝写入。同步进程使用项目内的操作系统文件锁，其他同步等待受限时间，进程结束后由操作系统释放锁。

写入前保存原文备份和 journal，然后逐文件原子替换。它不保证多个文件在整个文件系统中同时提交；中途 I/O 失败时，journal 保留已写入文件、未写入文件和失败状态，原文备份也保留。恢复前核对当前文件哈希，按文件恢复，不能盲目覆盖之后的新改动。

解析后的语义版本排除合法的生成导航区，保留身份、职责和人工正文。纯导航修复不应成为新的业务来源变化。原始文件字节哈希仍用于并发检查和写入审计。旧扫描器的算法标识升级为 `doctree-scan-v2`，不能把旧指纹当作新算法计算结果。

来源访问拒绝越界路径、符号链接、目录联接和不独立的可写文档。发现预算耗尽、文档超限和解析失败会报错，不能在不完整的发现结果上静默删除父子链接。扫描不导入或执行来源脚本，也不执行 Markdown 里的命令。

默认协议发现排除 `.git`、`.doctree` 与常见依赖、缓存目录，包含运行产物、历史和打包目录。默认上限为 32 层、20000 个目录、20000 份 Markdown、单份文档 1 MB、Markdown 总量 64 MB。可通过 `plan_sync(..., discovery_options=...)` 明确调整预算，计划会保存这些选项，应用时使用同一策略重新验证。发现超限或预期写入后将超限均拒绝生成部分计划。治理范围仅决定新增哪些节点，不应以浅层发现截断已经存在的深层节点。

## 5. 便携目录与日常命令

安装器创建 `.doctree/manage.py`、Agent 工具说明、三个标准库模块和树图页面。通过根 `.gitignore` 的有界区块忽略 `/.doctree/`；根 `AGENTS.md` 加入简短导航指引，并保留原有用户内容。重复安装相同内容不制造重复区块，替换已有文件前保存备份。

便携代码不依赖第一版扫描器、治理存储、PyYAML、数据库或外部服务。首次安装从工具仓库运行 `python -X utf8 -m doctree.portable --root <项目路径> install`，此入口本身也只需标准库；安装后通过 `annotate` 显式接入根和子目录。兼容命令 `python -m doctree install <项目路径>` 和工作区批量 `sync` 仍会加载旧版模块，因此需要 PyYAML。

根 Markdown 的 ID 和标题优先于本机注册提示。启动器根据自身位置找到项目，移动整个项目后可继续使用；Git 克隆没有携带被忽略的 `.doctree/` 时，从工具重新安装即可。

从被管理项目的根目录运行：

```powershell
python -X utf8 .doctree/manage.py serve --port 8768
python -X utf8 .doctree/manage.py tree
python -X utf8 .doctree/manage.py context --directory docs
python -X utf8 .doctree/manage.py sync --check
python -X utf8 .doctree/manage.py sync
python -X utf8 .doctree/manage.py annotate --directory docs --purpose "维护项目使用与设计说明" --check
python -X utf8 .doctree/manage.py annotate --directory docs --purpose "维护项目使用与设计说明"
```

便携 `sync --check` / `annotate --check` 只预览，退出码为 0 表示无需改动、1 表示有差异、2 表示失败；去掉 `--check` 后应用。`annotate` 显式接入一个已存在目录，入口可选 `README.md` 或 `DOCTREE.md`。更复杂的批量与自定义文件名使用工作区的选择文件接口。

`context` 返回目标目录说明、有文档入口的祖先和直接子节点说明，并保留原始文档内容。它提供阅读上下文，不声称完成自然语言审阅。`tree` 输出可重建 JSON，没有写入第一版 `state.json` 的依赖。

如果项目已有 `.doctree/state.json`、审阅记录或交付历史，安装不会删除它们。**工具代码和索引可重建，治理历史和恢复备份需要单独保留。** 不应将“加入 Git 忽略”理解为“可以删除整个管理目录”。

## 6. 人类页面

工作区服务的 `/`、`/tree` 为目录树，`/details` 保留第一版详细治理页面。便携包只携带树图与 Markdown 查看器，`/details` 回到树图；它不复制工作区的旧映射和治理数据库。

树图支持目录展开、搜索、拖动、缩放和自适应视图，可切换已接入分支与全部目录。点击节点可查看职责、父子信息和 Markdown；打开目录通过本机服务调用系统文件管理器。Markdown 内容作为数据处理，不执行来源 HTML 或脚本。

物理树默认最多展开三层、每项目最多 350 个节点；每目录最多读取 3000 个条目，每项目 Markdown 读取总预算为 16 MB。历史、批量产物和打包目录默认折叠内部，仍可沿 README 链接读取；隐藏目录和依赖不进入页面。超限会在返回数据中标明警告和不完整的计数或元数据，界面标注受限展开的节点，不能把展示范围当作完整审计范围。协议发现和网页展示使用不同预算：前者服务导航写入的完整性检查，后者控制人类页面规模。

当前更新提示是 Markdown 中的明确约定。网页刷新会重新读取文件，尚未提供跨机器“上级已吸收本次变化”的确认协议、持续监听或自动正文摘要。新增这些能力时，需要分别定义来源版本、人工确认、可重建索引和需要保留的历史，不能从一个“刷新成功”推出全部已核对。

## 7. 通用接入选项与 v1 边界

[selections.example.json](../config/selections.example.json) 提供通用的显式接入格式。使用前替换目录、ID、标题和职责，确认这些目录已存在；可以仅接入项目根，再逐步加入文档、源码或其他具有独立职责的目录。已存在的 README 保留正文，没有 README 的选定目录可以创建说明。

少量目录适合使用选择文件或便携 `annotate`；按深度治理适合先运行 `coverage`，再预览 `cover` 计划；完整覆盖需要明确选择完整范围。这些入口都不会默认搬动业务文件或执行来源程序。接入后的记录应列出真实文件变化、正文保留检查与未覆盖范围，不能以设计预期代替验证。

v1 详细页继续使用原配置中的语义映射和历史状态，不因 v2 物理目录树出现而改写为另一棵同义树。相同来源可以从目录视图进入，也可以从既有详细证据节点进入。新增的生成导航从语义版本中排除；身份和职责等真正的来源变更仍需在旧治理流程中如实表达，不能自动标成已审阅或已验收。

后续扩大接入时，先选少量具有不同职责的目录，记录遗漏、误分类、阅读收益和每次维护成本，再调整粒度。将项目特有的流程和验收字段保留在项目正文或可选扩展中，避免把某一类项目的规则硬编码成通用协议。
