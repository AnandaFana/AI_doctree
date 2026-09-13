<!-- doctree:node {"schema":2,"id":"sample.v2.tests","title":"未执行的教学用例"} -->
<!-- doctree:purpose:start -->
> 本目录职责：保存少量虚构字符串输入输出，帮助阅读接口约定，并明确这些条目不是测试运行结果。
<!-- doctree:purpose:end -->
<!-- doctree:nav:start -->
> 上级：[StringNote · v2 教学样例](../README.md)
> 子目录：暂无已接入子目录
> 更新约定：先更新本目录；影响范围、结论或下一步时核对上级 README。执行完成与业务/科学验收分别记录。
<!-- doctree:nav:end -->

# 未执行的教学用例

[cases.json](cases.json) 包含首尾空格、内部连续空格和首尾制表符/换行的示例。
`expected` 是教学输入的预期文本，不代表已经执行或通过测试。

维护回放不会导入或运行样例源码，也不会执行这些用例。这里保留这一边界，避免
把“DocTree 核对流程回归通过”误写成“文本处理功能通过业务验收”。
