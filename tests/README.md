<!-- doctree:node {"schema":2,"id":"doctree.tests","title":"回归验证"} -->
<!-- doctree:purpose:start -->
> 本目录职责：验证协议写入、读取预算、维护记录和旧治理状态的可核对边界。
<!-- doctree:purpose:end -->
<!-- doctree:nav:start -->
> 上级：[DocTree 工具](../README.md)
> 子目录：暂无已接入子目录
> 更新约定：先更新本目录；影响范围、结论或下一步时核对上级 README。执行完成与业务/科学验收分别记录。
<!-- doctree:nav:end -->

# 回归验证

从项目根运行 `python -X utf8 -m unittest discover -s tests -v`。测试使用临时夹具，不执行接入项目的科学计算。

[test_markdown_protocol.py](test_markdown_protocol.py)、[test_coverage.py](test_coverage.py) 覆盖原文保留和范围；[test_foldertree.py](test_foldertree.py) 验证直接上下文及预算；[test_maintenance.py](test_maintenance.py) 通过临时 Git 仓库验证遗漏提醒和逐级判断。

旧交付证据、完整扫描保护与接口由相应 governance/scanner/server 测试维护。新失败先保存复现，修复后验证实际行为，不用模板断言替代业务证据。
