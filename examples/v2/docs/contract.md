# 虚构接口约定

`src/formatter.py` 中的 `format_label(value)` 接收字符串，委托
`src/rules/whitespace.py` 中的 `trim_edges(value)` 返回清理后的字符串。
当前源码使用字符串的 `strip()` 方法移除首尾空白，内部空白保持原样。
样例不读取文件、不访问网络，也不提供命令行或生产服务。

下列输入输出只是阅读源码后写出的教学用例，放在
[cases.json](../tests/cases.json) 中；DocTree 回放不会运行这些用例。
类型异常、性能和真实用户场景均未做验证。

如果规则行为或公开入口发生变化，应由人或 Agent 核对本约定及受影响的
目录说明。Git 文件变化只能提示核对，不能自动认定这些约定仍成立。
