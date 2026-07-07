请创建小写表名 `s`、`p`、`j`、`spj`，对应供应商、零件、工程项目、供应情况。

要求：

- `s.sno`、`p.pno`、`j.jno` 为主键；
- `spj` 包含 `sno,pno,jno,qty`；
- `spj` 以 `(sno,pno,jno)` 为联合主键；
- `spj.sno/pno/jno` 分别引用 `s/p/j`。
