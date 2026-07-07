# Review 2026 SQL Problem Packages

These packages are generated from the exam review scope, `program.md`, and the referenced SPJ/PDF lab materials.

Import zip files from `examples/review-2026/zips/` in the admin page. Each problem has one public sample and two hidden test cases.

| # | Slug | Title | Tags |
| --- | --- | --- | --- |
| 1 | `review-01-select-logical-order` | SELECT 逻辑执行顺序综合查询 | SELECT, WHERE, GROUP BY, HAVING, ORDER BY, LIMIT |
| 2 | `review-02-where-conditions` | WHERE 多条件筛选 | WHERE |
| 3 | `review-03-like-in-between-null` | LIKE、IN、BETWEEN、IS NULL | LIKE, IN, BETWEEN, IS NULL |
| 4 | `review-04-distinct-courses` | DISTINCT 去重 | DISTINCT |
| 5 | `review-05-order-by-employees` | ORDER BY 多列排序 | ORDER BY |
| 6 | `review-06-limit-offset-articles` | LIMIT OFFSET 分页 | LIMIT, OFFSET |
| 7 | `review-07-aggregate-payments` | 聚合函数统计 | COUNT, SUM, AVG, MAX, MIN |
| 8 | `review-08-group-by-region-sales` | GROUP BY 分组汇总 | GROUP BY, SUM |
| 9 | `review-09-having-customers` | HAVING 分组筛选 | GROUP BY, HAVING |
| 10 | `review-10-join-employees-departments` | JOIN 连接查询 | JOIN |
| 11 | `review-11-subquery-above-dept-average` | 子查询：高于部门平均工资 | Subquery |
| 12 | `review-12-set-operations` | UNION、INTERSECT、EXCEPT 集合查询 | UNION, INTERSECT, EXCEPT |
| 13 | `review-13-dml-bonus-salary` | DML：给获奖员工当前薪水加 10% | UPDATE, DML, program.md |
| 14 | `review-14-ddl-spj-schema` | DDL：创建 SPJ 表并定义主外键 | CREATE, PRIMARY KEY, FOREIGN KEY, SPJ, PDF |
| 15 | `review-15-alter-drop-truncate` | ALTER、DROP、TRUNCATE 综合操作 | ALTER, DROP, TRUNCATE |
| 16 | `review-16-constraints-default-check` | 完整性约束：非空、唯一、检查、默认值 | Constraint, DEFAULT, CHECK, UNIQUE |
| 17 | `review-17-view-actor-name` | 视图：actor_name_view | VIEW, program.md |
| 18 | `review-18-index-orders` | 索引 INDEX | INDEX |
| 19 | `review-19-grant-revoke-public` | GRANT、REVOKE 对象权限 | GRANT, REVOKE, DCL, PDF |
| 20 | `review-20-transaction-rollback` | COMMIT、ROLLBACK 事务控制 | COMMIT, ROLLBACK, TCL |
| 21 | `review-21-sql-command-categories` | DDL、DML、DQL、DCL、TCL 分类 | DDL, DML, DQL, DCL, TCL |
| 22 | `review-22-dept-emp-string-agg` | 按照 dept_no 汇总员工编号 | GROUP BY, string_agg, program.md |
| 23 | `review-23-name-suffix-order` | 按照 first_name 最后两个字母排序 | ORDER BY, string function, program.md |
| 24 | `review-24-latest-hire-employee` | 查找最晚入职员工 | Subquery, program.md |
| 25 | `review-25-starting-salary` | 查找所有员工入职时薪水 | JOIN, program.md |
| 26 | `review-26-current-manager-salary` | 获取当前部门经理当前薪水 | JOIN, program.md |
| 27 | `review-27-current-employee-manager` | 获取所有员工当前 manager | JOIN, program.md |
| 28 | `review-28-dept-raise-count` | 统计各部门员工涨薪次数总和 | Window, GROUP BY, program.md |
| 29 | `review-29-current-title-avg-salary` | 统计当前 title 的当前平均薪水 | AVG, JOIN, program.md |
| 30 | `review-30-spj-red-suppliers-j1` | SPJ：供应 J1 红色零件的供应商 | SPJ, JOIN, PDF |
| 31 | `review-31-spj-no-tianjin-red-projects` | SPJ：没有使用天津供应商红色零件的工程 | SPJ, Subquery, PDF |
| 32 | `review-32-spj-division-s1-parts` | SPJ：至少使用 S1 供应全部零件的工程 | SPJ, Division, HAVING, PDF |
| 33 | `review-33-spj-sanjian-view` | SPJ：为三建工程创建供应视图 | SPJ, VIEW, PDF |
| 34 | `review-34-acid-classification` | 事务 ACID 特性判断 | ACID, Theory |
| 35 | `review-35-normal-form-classification` | 函数依赖与范式判断 | FD, Normal Form, Theory |
| 36 | `review-36-er-to-relations` | E-R 图向关系模式转换 | E-R, Theory |
| 37 | `review-37-three-schema-levels` | 外模式、模式、内模式分类 | Three Schema, Theory |
