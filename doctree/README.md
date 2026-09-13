<!-- doctree:node {"schema":2,"id":"doctree.engine","title":"工具实现"} -->
<!-- doctree:purpose:start -->
> 本目录职责：实现目录协议、路径读取、便携安装与文档核对；保留旧详细治理兼容。
<!-- doctree:purpose:end -->
<!-- doctree:nav:start -->
> 上级：[DocTree 工具](../README.md)
> 子目录：暂无已接入子目录
> 更新约定：先更新本目录；影响范围、结论或下一步时核对上级 README。执行完成与业务/科学验收分别记录。
<!-- doctree:nav:end -->

# 工具实现

[markdown_protocol.py](markdown_protocol.py) 负责显式节点注入和原文保护；[foldertree.py](foldertree.py) 提供界面投影和有预算的直接上下文读取；[coverage.py](coverage.py) 检查选定范围与全局导航完整性。

[maintenance.py](maintenance.py) 提供可选 Git 收尾检查和人或 Agent 的核对记录；[portable.py](portable.py) 打包标准库运行时。旧详细页使用 [scanner.py](scanner.py)、[governance.py](governance.py)、[server.py](server.py) 和 [cli.py](cli.py)。

改变来源版本、范围或记录含义时同步协议与回归；不从工具成功推导业务验收，不在便携模块引入旧治理或第三方包。
