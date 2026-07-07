给定订单表 `orders`，查询 2026 年以来已支付订单中，总金额不少于 100 的客户。

输出列：

| 列名 | 含义 |
| --- | --- |
| `customer_id` | 客户编号 |
| `total_amount` | 已支付订单总金额，保留两位小数 |
| `order_count` | 已支付订单数 |

按 `total_amount` 降序、`customer_id` 升序输出，最多输出前 3 行。
