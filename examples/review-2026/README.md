# Review 2026 SQL Problem Packages

These packages are the high-priority SQL subset generated from the exam review scope, `program.md`, the referenced SPJ/PDF lab materials, and the 2025 A paper.

Kept topics: JOIN, nested subquery, GROUP BY/HAVING, EXISTS, NOT EXISTS, IN/NOT IN, division-style queries, UPDATE, and VIEW.

Import zip files from `examples/review-2026/zips/` in the admin page. Each problem has one public sample and two hidden test cases.

| # | Slug | Title | Tags |
| --- | --- | --- | --- |
| 1 | `review-09-having-customers` | HAVING 分组筛选 | GROUP BY, HAVING |
| 2 | `review-10-join-employees-departments` | JOIN 连接查询 | JOIN |
| 3 | `review-11-subquery-above-dept-average` | 子查询：高于部门平均工资 | Subquery |
| 4 | `review-13-dml-bonus-salary` | DML：给获奖员工当前薪水加 10% | UPDATE, DML, program.md |
| 5 | `review-17-view-actor-name` | 视图：actor_name_view | VIEW, program.md |
| 6 | `review-24-latest-hire-employee` | 查找最晚入职员工 | Subquery, program.md |
| 7 | `review-25-starting-salary` | 查找所有员工入职时薪水 | JOIN, program.md |
| 8 | `review-26-current-manager-salary` | 获取当前部门经理当前薪水 | JOIN, program.md |
| 9 | `review-27-current-employee-manager` | 获取所有员工当前 manager | JOIN, program.md |
| 10 | `review-29-current-title-avg-salary` | 统计当前 title 的当前平均薪水 | AVG, JOIN, program.md |
| 11 | `review-30-spj-red-suppliers-j1` | SPJ：供应 J1 红色零件的供应商 | SPJ, JOIN, PDF |
| 12 | `review-31-spj-no-tianjin-red-projects` | SPJ：没有使用天津供应商红色零件的工程 | SPJ, Subquery, NOT EXISTS, PDF |
| 13 | `review-32-spj-division-s1-parts` | SPJ：至少使用 S1 供应全部零件的工程 | SPJ, Division, NOT EXISTS, HAVING, PDF |
| 14 | `review-33-spj-sanjian-view` | SPJ：为三建工程创建供应视图 | SPJ, VIEW, PDF |
| 15 | `review-38-lab-zhang-teacher-experiments` | A卷：查询张sir带过的实验名称 | JOIN, Exam |
| 16 | `review-39-lab-senior-teachers-without-records` | A卷：没有带过实验课的高工 | NOT EXISTS, Subquery, Exam |
| 17 | `review-40-lab-teachers-all-category-c` | A卷：指导过全部 C 类实验的实验师 | Division, NOT EXISTS, Subquery, Exam |
| 18 | `review-41-lab-students-at-least-two` | A卷：至少做过 2 个实验的学生 | GROUP BY, HAVING, Exam |
| 19 | `review-42-lab-overtime-score-update` | A卷：超时完成实验扣分 | UPDATE, JOIN, Exam |
| 20 | `review-43-lab-lowest-score-records` | A卷：查询最低成绩实验记录 | Subquery, Exam |
| 21 | `review-44-lab-teacher-student-count` | A卷：统计每个实验师带过的学生人数 | GROUP BY, JOIN, Exam |
| 22 | `review-45-lab-student-summary-view` | A卷：建立学生实验统计视图 VW1 | VIEW, GROUP BY, Exam |
| 23 | `review-46-spj-exists-heavy-red-suppliers` | EXISTS：供应过大批量红色零件的供应商 | EXISTS, SPJ, Subquery |
| 24 | `review-47-spj-in-red-part-suppliers` | IN：供应过红色零件的供应商 | IN, SPJ, Subquery |
| 25 | `review-48-lab-not-exists-no-b-experiment` | NOT EXISTS：没有做过 B 类实验的学生 | NOT EXISTS, Subquery, Exam |
| 26 | `review-49-spj-not-in-no-tianjin-supplier-projects` | NOT IN：没有使用天津供应商的工程 | NOT IN, SPJ, Subquery |
| 27 | `review-50-lab-division-students-all-c` | 除法思想：完成全部 C 类实验的学生 | Division, NOT EXISTS, Subquery, Exam |
