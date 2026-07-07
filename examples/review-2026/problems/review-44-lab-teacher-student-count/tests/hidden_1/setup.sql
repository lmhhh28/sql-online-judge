create table lab_teachers (
  teacher_id text primary key,
  teacher_name text not null,
  title text not null,
  phone text
);

create table students (
  student_id text primary key,
  student_name text not null,
  class_name text not null,
  major text not null
);

create table experiments (
  experiment_id text primary key,
  experiment_name text not null,
  required_minutes integer not null,
  category text not null
);

create table experiment_records (
  student_id text not null references students(student_id),
  experiment_id text not null references experiments(experiment_id),
  teacher_id text not null references lab_teachers(teacher_id),
  actual_minutes integer not null,
  score integer not null,
  primary key (student_id, experiment_id)
);

insert into lab_teachers values
  ('T1', '张sir', '高工', '13800000001'),
  ('T2', '李sir', '工程师', '13800000002'),
  ('T3', '王sir', '高工', '13800000003'),
  ('T4', '赵sir', '高工', '13800000004'),
  ('T5', '陈sir', '教授', '13800000005');

insert into students values
  ('S1', '小王', '计科2301', '计科'),
  ('S2', '小李', '计科2301', '计科'),
  ('S3', '小周', '软件2302', '软件'),
  ('S4', '小陈', '网络2301', '网络');

insert into experiments values
  ('E1', 'SQL基础', 90, 'A类'),
  ('E2', '嵌套查询', 120, 'C类'),
  ('E3', '视图设计', 100, 'C类'),
  ('E4', '事务恢复', 80, 'B类'),
  ('E5', '连接查询', 60, 'C类');

insert into experiment_records values
  ('S1', 'E1', 'T1', 90, 85),
  ('S1', 'E2', 'T1', 130, 80),
  ('S1', 'E3', 'T2', 100, 91),
  ('S1', 'E5', 'T1', 70, 88),
  ('S2', 'E2', 'T2', 118, 76),
  ('S2', 'E3', 'T2', 150, 60),
  ('S2', 'E5', 'T2', 58, 84),
  ('S3', 'E4', 'T1', 70, 70),
  ('S3', 'E2', 'T5', 140, 59),
  ('S4', 'E5', 'T5', 55, 92);

insert into experiment_records values ('S4', 'E1', 'T1', 90, 81);
