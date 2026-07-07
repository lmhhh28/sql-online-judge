给定关系模式案例表 `nf_cases`：

- `all_attrs_atomic=false` 表示未达到 1NF，输出 `UNF`；
- 存在部分函数依赖时，最高只到 `1NF`；
- 无部分依赖但存在传递依赖时，最高到 `2NF`；
- 三者都满足时输出 `3NF`。

输出 `case_id,highest_nf`。
