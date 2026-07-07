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
- `ordered`: `true` means row order must match
- folders whose names start with `sample_` are public; all other folders are hidden

## Test Case Files

- `setup.sql`: creates and seeds the testcase database
- `judge.sql`: returns the result to compare
- `expected.csv`: expected output, including the header row

For query-only problems, set `judge.sql` to:

```text
__USER_OUTPUT__
```

The judge then compares the user's final result set directly.

