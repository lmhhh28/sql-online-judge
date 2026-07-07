# Problem Package Format

Each problem is imported as a zip file:

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

## `problem.yaml`

```yaml
slug: employee-query
title: 高薪员工查询
difficulty: 入门
tags: [SELECT]
checker: result
ordered: true
time_limit_ms: 3000
```

- `checker`: `result` or `string`
- `ordered`: `true` means row order must match; column names and column order always must match
- folders whose names start with `sample_` are public; all other folders are hidden

## Test Case Files

- `setup.sql`: creates and seeds the testcase database
- `judge.sql`: returns the result to compare
- `expected.csv`: expected output, including the header row
- SQL `NULL` is represented as `\N`; the literal text `NULL` is treated as a string
- Result values are compared as exact CSV text after PostgreSQL formatting; numeric-looking values and whitespace are not normalized, so `1`, `1.0`, and `1 ` are different

For query-only problems, set `judge.sql` to:

```text
__USER_OUTPUT__
```

The judge then compares the user's final result set directly. User SQL for this mode must return exactly one result set.
