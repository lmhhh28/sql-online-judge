请创建表 `audit_log`，用于记录系统操作日志。

要求列结构如下：

| 列名 | 类型 | 是否允许 NULL |
| --- | --- | --- |
| `id` | `integer` | 否 |
| `action` | `text` | 否 |
| `created_at` | `timestamp` | 否 |

