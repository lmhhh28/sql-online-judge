给定 E-R 转换片段 `er_items`，按本题规则输出需要新增的关系模式数量 `extra_relations`：

- `strong_entity`、`weak_entity`：自身形成 1 个关系；
- `one_to_many`：外键并入多方，新增 0 个关系；
- `many_to_many`：联系本身新增 1 个关系；
- `multivalued_attribute`：新增 1 个关系。

输出 `item_id,extra_relations`。
