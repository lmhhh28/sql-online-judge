创建表 `course_registrations`：

- `id integer primary key`
- `student_id integer not null`
- `course_code text not null`
- `credits integer not null default 3`
- `credits` 必须在 1 到 6 之间
- `(student_id, course_code)` 唯一
