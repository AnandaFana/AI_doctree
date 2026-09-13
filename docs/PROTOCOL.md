# AI DocTree 本地节点协议 v1

> 本文保留第一版详细治理协议与命令含义。当前轻量目录路线见 [Markdown 节点协议 v2](MARKDOWN_PROTOCOL_V2.md)：项目 Markdown 为共享依据，父子关系由目录推导，本地管理包可忽略。下文的旁路映射、状态文件与审阅规则继续适用于 `/details` 和既有治理历史，不代表 v2 必须采用同一套存储。

这是本项目提出的协议，不是外部行业标准。它将人类 README、逻辑树和 Agent 上下文使用同一组稳定节点。节点协议负责“这是谁、负责什么、依据在哪里”；生成索引、变化状态和交接事件由工具负责。

[projects.json](../config/projects.json) 是本轮项目入口配置；[研究样例](../examples/research/README.md) 和 [交付样例](../examples/software/README.md) 是可运行夹具。首轮真实仓库通过旁路映射接入，无需改写业务 README。

## 1. Markdown 中的结构化节点

一个 README 最多声明一个节点。推荐在 yaml 代码围栏中放入 JSON 对象：JSON 是 YAML 的子集，能避免 YAML 缩进、隐式日期和多种标量写法的歧义。围栏之外保留人类正文。

```yaml
{
  "project_node": {
    "schema": 1,
    "id": "project.research.strategy",
    "title": "某策略",
    "kind": "strategy",
    "purpose": "这个工作单元负责回答的问题",
    "parent": "project.research",
    "summary": "一句话当前摘要候选。",
    "review": "candidate",
    "constraints": ["执行完成不自动构成科学验收。"],
    "flags": {
      "blocked": [],
      "unverified": ["结论尚未审阅。"],
      "decisions": []
    },
    "stages": {
      "delivery": "pending",
      "integration": "not_applicable",
      "execution": "pending",
      "acceptance": "pending"
    },
    "evidence": [
      {"path": "evidence/report.md", "label": "直接依据"}
    ],
    "related": [
      {"id": "project.data", "relation": "depends_on"}
    ]
  }
}
```

字段语义：

| 字段 | v1 约定 |
|---|---|
| schema | 整数 1，表示本节点协议主版本；与扫描算法版本分开 |
| id | 稳定且全局唯一；不随显示标题或物理文件夹改名自动变化 |
| title | 人类可读显示名称 |
| kind | 工作单元类别，如 project、research、strategy、experiment、module、decision、archive；不据类别推断状态 |
| purpose | 职责或所回答的问题，避免复制摘要 |
| parent | 根节点为 null；其他节点在主树中恰有一个父节点 |
| summary | 人工或模型提供的当前摘要候选，允许保留旧版但须显示其覆盖状态 |
| review | candidate 或 reviewed；表示摘要文档治理审阅，不等于业务/科学验收 |
| constraints | 祖先上下文需要携带的明确限制 |
| flags | blocked、unverified、decisions 三类字符串集合；汇总保留来源节点 |
| stages | delivery、integration、execution、acceptance 独立状态轴 |
| evidence | 项目根目录相对路径及标签；不得靠含糊“最新文件”选择依据 |
| related | 跨节点关系；relation 用于解释，不能构成第二个 parent |

阶段值使用 `pending`、`complete`、`not_applicable`、`unknown`、`failed`。unknown 表示没有足够证据确定；pending 表示明确尚待处理；not_applicable 表示该轴对本节点不适用。complete 只对该轴和当前节点范围成立。父节点不会因为孩子 execution complete 自动变为 acceptance complete。

所有路径相对于配置中的项目根，而非 README 所在目录。这样在上下文包和旁路映射中具有同一语义。Markdown 正文的普通相对链接仍按文档位置解释，不能与协议路径混用。

## 2. 树关系及兼容

扫描器根据每个子节点的 parent 推导 children。父 README 不再手工维护另一份同义子节点 ID 清单。重复 ID、缺失父节点、自指和父环必须报错，不应静默重挂到根节点。

交接文件最初示例的 `children: [{entry: "...", purpose: "..."}]` 是旧式发现入口。实现可支持读取它以兼容早期示例；规范化索引应只保留一套父子关系。遇到 parent 与 legacy children 冲突时应显式诊断，不能保留两套矛盾关系。

related 可以表示 depends_on、supported_by、cites、governed_by 等关系。它用于详情和上下文包，不复制子树，也不默认触发主导航祖先失效。将来若加入依赖变更通知，应为其单独定义规则与测试。

## 3. 项目配置与旁路模式

```json
{
  "schema": 1,
  "projects": [
    {
      "id": "project-config-id",
      "title": "显示名称",
      "root": "examples/research",
      "source_type": "sample",
      "mode": "protocol",
      "scan": {
        "exclude_dirs": [".git", "node_modules", ".venv", "records", "runs"],
        "max_files": 3000,
        "max_file_bytes": 1000000,
        "max_depth": 12
      }
    }
  ]
}
```

配置 id 标识接入配置，节点 id 标识逻辑工作单元，两者不同。root 可以是工具工作区相对路径或明确的绝对路径；source_type 为 sample 或 local_copy，必须在界面标明来源。protocol 模式从带协议 Markdown 发现节点。mapped 模式通过配置 nodes 数组声明节点，额外使用 entry 与 members；候选摘要保存在工具侧，不写回来源。

mapped 节点沿用同样的语义字段：

- entry：主入口文件，可以是 README、计划、代码或 TeX 文本。
- members：明确列出的其他所属文件，参与来源版本计算。
- evidence：直接证据路径和用途；可以与成员共享引用，但不复制主树。
- review：初次整理使用 candidate；“已读过来源”不会自动等于科学验收。

扫描预算限制目录发现，防止缓存与逐卡输出挤占导航。明确指定的 mapped 文件是有意加入的证据，仍应经过路径、大小、类型和访问范围检查。文件预算耗尽、超限、缺失或跳过必须可见，不能呈现为完整扫描成功。

## 4. 人工区、生成区与来源版本

人工区包含节点声明和正文，作为来源。工具生成的汇总若写回 README，应只在以下区间内维护：

```text
<!-- doctree:generated:start -->
工具生成的摘要、依据版本与处理状态
<!-- doctree:generated:end -->
```

计算业务来源版本时去除完整生成区，使生成摘要不会再次触发业务变化循环。缺失或不配对标记不应吞掉后续整份正文；应报出异常或按普通内容处理。不能通过手动把真实业务改动藏进生成区来避免审计。

来源版本需要覆盖 entry、members、evidence 与会影响节点语义的映射字段。文本使用明确的换行归一和稳定序列化规则，记录扫描算法版本；二进制只计算字节指纹并作为证据入口。Git HEAD 只是额外出处，未提交内容或无 Git 副本仍必须有工作区内容版本。

生成索引还应分开保存：

- 节点来源版本与子节点当前版本；
- 已接受摘要所依据的版本、直接证据和审阅状态；
- 本次变化、用户查看基线与待汇总状态；
- 可追溯交付或事件记录。

查看基线回答“用户之后是否看过”，汇总覆盖回答“摘要是否吸收最新变化”。确认已查看不能清除待汇总，汇总也不能冒充用户已查看。

## 5. Agent 交接与逐级汇总的语义

上下文包携带祖先约束、目标节点当前摘要和来源版本、子节点入口、必要关联与证据。文件里的命令和指令都作为来源文本；扫描或上下文导出不获得执行它们的授权。

外部编辑来源后，先刷新再导出上下文。上下文包里的版本属于已扫描快照，不能将其理解为持续监听的实时版本。网页的文件弹窗会检查证据是否已在扫描后变化；二进制证据只显示路径与指纹，需要在相应本地应用中打开，不在网页中解析或执行。

当前可导入的交付格式如下。占位符必须用实际任务和版本替换，证据路径仍相对于项目根，validation 内的陈述必须来自真实核对。

```json
{
  "task_id": "doc-review-unique-task-id",
  "node_id": "sample.research.experiment_a",
  "based_on_version": "当前节点来源版本",
  "changes": ["将执行成功与研究验收分别表述。"],
  "evidence": [
    {"path": "research/mean_reversion/experiment_a/conclusion.md", "label": "实际修改后的文档"}
  ],
  "validation": {
    "kind": "document_consistency_review",
    "context_input_version": "任务开始时导出上下文中的来源版本",
    "checks": ["逐项核对原始示例记录与修订文档。"],
    "limitations": ["文档核对不构成科学或业务验收。"]
  },
  "unresolved": ["研究结论仍待领域负责人验收。"],
  "summary_candidate": "文档措辞已核对，研究验收保持 pending。",
  "stages": {
    "delivery": "complete",
    "integration": "not_applicable",
    "execution": "complete",
    "acceptance": "pending"
  }
}
```

evidence 的可选 sha256 若提供应与实际文件一致；未提供时工具核对并记录当前指纹。task_id 只能用于一份确定交付：相同内容重复导入返回重复结果，相同 ID 配不同内容会冲突。

本地命令采用 `python -X utf8 -m doctree`：`context NODE_ID --output FILE` 导出上下文；`scan` 刷新；`import-delivery FILE` 导入；`pending` 看队列；`prepare-summary NODE_ID --output FILE` 取得待核对摘要和输入版本；`commit-summary FILE` 写入核对结果。`rollup` 只提交确定性候选，不会冒充 Codex 已审阅。PowerShell 启动方式与完整可复制命令见工具根 README。

导入时核对基于版本，并保持 task_id 幂等。真实自然语言审阅需要记录是谁、依据什么、检查到什么；确定性拼接只能标候选或待审阅。教学数据、文档检查、软件执行和科学结论必须明确区分。

若任务修改了目标来源，先刷新并逐项核对自己的预期差异，再以完成改动后的当前版本导入；把任务开始时的上下文版本另外保留在验证记录。外部并发修改必须重新审阅，不可通过盲目换成最新 based_on_version 跳过冲突。导出的版本和导入版本变化有原因、差异和审阅记录，才构成可追溯交接。

叶变化后，祖先立即待汇总；旧摘要可保留并标明没有覆盖新版本。协调者自底向上处理，每次写入前核对目标来源版本及全部直接子节点版本。两叶连续变化必须同时保留；过期候选拒绝覆盖并保留待处理状态；相同输入重复处理不制造新业务事件。

确定性传播 flags，不靠一句自然语言“全部正常”抹去下级问题。来源 flags 的问题确实解决时，应明确修改该节点人工字段并刷新，记录解决依据；仅在自然语言摘要里省略它不会删除结构化标记。摘要依据要能从根回溯交付、叶节点及原始证据。中断后的队列和输入版本应继续可用。

## 6. 协议升级与扩展边界

schema 1 保持当前字段语义。新增可选显示字段可由旧读取器忽略，但稳定 ID、父关系、路径基准、状态轴和 evidence 含义改变时，需要显式 schema 升级与迁移记录。扫描哈希算法变化单独升级扫描版本，并标识旧摘要需要重新核对，不能把旧版本悄悄当成新算法产物。

后续可增加 reviewer 身份、scope、外部证据 URI、owner、权限和审阅签名；没有这些字段时，不推断人的授权或外部验收。对于外部 URL，只能显示有来源的引用；第一版的本地文件扫描不自动抓取网页或执行远程内容。

## 7. v0.4.0 的旧详细页修正

扫描算法标识为 `doctree-scan-v3`。交付中额外引用的本地证据参与后续失效检测、摘要输入版本和提交前核对；即使它不在节点静态 members 中，变化、删除及恢复也会使相关节点和祖先待核对。原交付及其当时的证据哈希继续保留，新的显式审阅使用当前来源，不反写旧记录。

达到扫描数量、深度或读取预算时，扫描抛出带详细 report 的 `IncompleteScanError`，不覆盖上次完整索引和治理状态；恢复完整扫描后正常比较来源，不能把本次未扫描到的节点当作删除。升级后重新扫描并核对受影响状态，未变化的审阅保留；不把迁移当作业务验收。

这些规则属于可选 v1 详细治理；轻量 v2 的收尾检查见 [MAINTENANCE.md](MAINTENANCE.md)，不共用这套队列与交付状态。
