实验管理数据库包含实验师、学生、实验、实验记录四张表。

查询没有做过任何 `B类` 实验的学生。

建议使用 `NOT EXISTS`，注意学生没有实验记录时也应视为“没有做过 B 类实验”。

输出 `student_id,student_name`，按 `student_id` 升序。
