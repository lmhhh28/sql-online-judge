# 题目包格式

管理员导入接口接收 ZIP 文件的原始字节，请使用：

```http
Content-Type: application/zip
```

请求体不是 `multipart/form-data`。后端也兼容 `application/x-zip-compressed` 和 `application/octet-stream`，但客户端应优先使用 `application/zip`。导入同一 `slug` 会替换原题目的测试用例；若该题仍有 `PENDING` 或 `RUNNING` 提交，导入会以冲突错误拒绝。

## 目录结构

```text
problem.yaml
statement.md
schema.md
tests/
  sample_1/
    setup.sql
    judge.sql
    expected.csv
  hidden_1/
    setup.sql
    judge.sql
    expected.csv
```

要求如下：

- `problem.yaml`、`statement.md` 必须位于 ZIP 根目录；`schema.md` 可省略。
- 至少包含一个 `tests/<名称>/` 测试用例，最多 100 个。
- 每个测试用例必须有非空的 `judge.sql` 和 `expected.csv`；`setup.sql` 可省略。
- 测试名最长 120 个字符，首字符必须是 ASCII 字母或数字，其余字符只允许 ASCII 字母、数字、下划线、点和连字符。
- 名称以 `sample_` 开头的测试用例是公开用例，其他名称均为隐藏用例。
- 所有文本文件必须是有效 UTF-8。

## `problem.yaml`

示例：

```yaml
slug: employee-query
title: 高薪员工查询
difficulty: 入门
tags: [SELECT]
checker: result
ordered: true
time_limit_ms: 3000
is_active: true
case_score: 1
```

YAML 使用安全且严格的加载规则：顶层必须是字符串键的映射；拒绝重复键、别名/锚点引用和未知字段；布尔值、整数、数字、字符串与列表不会互相隐式代替。允许的字段只有：

| 字段 | 类型与默认值 | 限制 |
| --- | --- | --- |
| `slug` | 必填字符串 | 2–80 个字符；只允许小写 ASCII 字母、数字和分隔单词的单个连字符，不能以连字符开头或结尾 |
| `title` | 必填字符串 | 去除首尾空白后为 1–200 个字符 |
| `difficulty` | 字符串，默认 `入门` | 1–32 个字符 |
| `tags` | 字符串列表，默认 `[]` | 最多 32 个；每项去除首尾空白后非空、最长 64 个字符且不得重复 |
| `checker` | 字符串，默认 `result` | 只能是 `result` 或 `string` |
| `ordered` | 布尔值，默认 `false` | 必须写为 YAML 布尔值 |
| `time_limit_ms` | 整数，默认 `3000` | 100–30000 毫秒 |
| `is_active` | 布尔值，默认 `true` | 导入后是否立即对普通用户开放 |
| `case_score` | 数字，默认 `1` | 每个用例统一使用该分数；必须有限且位于 0–1000000 |

`statement.md` 与 `schema.md` 各自最多 1,000,000 个字符，同时其 UTF-8 编码也各自不得超过 1,000,000 字节。

前端按安全 Markdown 渲染这两个文件：原始 HTML 会被忽略，Markdown 图片（包括远程图片和 data URL）会显示为文字占位，避免题目内容加载第三方跟踪资源。链接仍可点击，但浏览器不会发送来源页面地址。

## 测试文件

- `setup.sql`：创建本用例所需表、数据和其他数据库内对象。它在用户 SQL 前执行。
- `judge.sql`：在用户 SQL 后执行，并返回唯一一个用于比较的结果集。
- `expected.csv`：包含表头行的预期输出。

每个测试用例都在全新的临时 PostgreSQL 数据库中执行，因此一个用例的 DDL/DML 不会延续到下一个用例。用户 SQL 可包含普通 DML 和 DDL；系统只拦截会突破临时数据库边界、操纵服务端或改变受保护会话状态的高风险 SQL。

如果题目要直接比较用户最后返回的结果集，把 `judge.sql` 的全部内容设为：

```text
__USER_OUTPUT__
```

该模式下，用户 SQL 必须且只能返回一个结果集；没有结果集或返回多个结果集都会判错。普通 `judge.sql` 也必须返回且只返回一个结果集。

## `expected.csv` 与比较规则

所有 checker 的 `expected.csv` 都必须是合法、矩形的 CSV：

- 必须有表头记录并至少包含一列；每个非空数据记录的列数必须与表头相同。
- 最多 256 列、10,000 个数据行（表头不计入数据行）。
- UTF-8 总大小最多 1,000,000 字节；单个 CSV 字段也受 1,000,000 字符的解析上限约束。
- SQL `NULL` 写作 `\N`；文字 `NULL` 是普通字符串。
- CSV 中的引号、逗号和换行遵循标准 CSV 转义；不要手工用反斜杠转义逗号或引号。

`checker: result` 时：

- 列名和列顺序始终必须完全一致。
- `ordered: true` 要求数据行顺序完全一致；`false` 只忽略行顺序，不忽略重复行。
- 除 `\N` 表示 SQL `NULL` 外，单元格按 PostgreSQL 输出后的精确文本比较，不做数字或空白归一化；`1`、`1.0` 和 `1 ` 不相等。

`checker: string` 时，实际 CSV 文本与 `expected.csv` 做整体精确比较，只统一 Windows `CRLF` 与 Unix `LF` 换行，不做其他归一化。

## 导入资源限制

为防止 ZIP 炸弹和超大题目占用本机资源，导入同时应用以下限制：

| 项目 | 上限 |
| --- | ---: |
| HTTP 请求体/压缩 ZIP | 10 MiB |
| ZIP 条目数量（包含目录条目） | 256 |
| 单个 ZIP 条目声明大小 | 2 MiB |
| 所有条目声明的解压后总大小 | 16 MiB |
| `problem.yaml` | 64 KiB |
| 测试用例数 | 100 |
| `expected.csv` | 1,000,000 字节、10,000 数据行、256 列 |

ZIP 条目只支持不压缩（stored）或 Deflate 压缩。ZIP 中不得包含重复条目、加密条目、绝对路径、反斜杠路径、空路径段、`.`/`..` 路径段或 NUL 字符。不要依赖 ZIP 内顶层父目录；规定文件必须直接位于 ZIP 根目录。

这些是导入阶段的限制。判题阶段还会独立限制时间、临时数据库大小和实际查询输出，见[判题安全说明](judge-security.md)。

## `run`、`submit` 与隐藏数据

- `run` 只选择 `sample_` 开头的公开测试用例；题目没有公开用例时会拒绝运行。
- `submit` 选择全部公开与隐藏用例；题目完全没有用例时会拒绝提交。
- 普通用户读取题目时，只会看到公开用例及其 `expected.csv`。
- 普通用户查看自己的提交详情时，隐藏用例的名称会替换为通用名称，`actual_output` 与 `expected_output` 均为空，耗时归零，消息也会泛化；管理员可查看未做隐藏脱敏的细节，但持久化与响应仍受输出截断或省略上限约束。
- 提交列表只返回摘要，不携带 SQL 代码或逐用例输出；提交详情只允许提交者本人或管理员读取。

单个用户最多同时拥有 3 个 `PENDING`/`RUNNING` 提交。
