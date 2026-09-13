<!-- doctree:node {"schema":2,"id":"sample.v2.src.rules","title":"空白清理规则"} -->
<!-- doctree:purpose:start -->
> 本目录职责：维护单条字符串首尾空白清理规则、源码注释与其阅读入口，判断变动是否需要上级核对。
<!-- doctree:purpose:end -->
<!-- doctree:nav:start -->
> 上级：[文本处理入口](../README.md)
> 子目录：暂无已接入子目录
> 更新约定：先更新本目录；影响范围、结论或下一步时核对上级 README。执行完成与业务/科学验收分别记录。
<!-- doctree:nav:end -->

# 空白清理规则

[whitespace.py](whitespace.py) 的 `trim_edges(value)` 当前使用字符串的 `strip()`
方法移除首尾空白，保留内部空白。这里仅包含一条虚构规则。

仅调整局部变量或澄清相同含义的注释时，本页可能仍然准确。人或 Agent 应读取
差异并记录具体理由，不能因为源码变化就自动改写 README，也不能因为函数名没变
就自动确认无需改动。

若变动需要上级重新核对调用入口或职责边界，显式记录 `parent_impact=needed`；
这只提示直接父节点。父节点仍须独立决定是否继续上报。
