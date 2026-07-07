按顺序完成以下操作：

1. 给 `stage_orders` 增加 `status text not null default 'new'` 列；
2. 清空 `stage_orders` 原有数据；
3. 插入一行 `(99, 500)`，让 `status` 使用默认值；
4. 删除表 `obsolete_cache`。
