```sql
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
```
