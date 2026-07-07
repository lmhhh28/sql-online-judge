```sql
create table departments (
  dept_no char(4) primary key,
  dept_name text
);

create table dept_emp (
  emp_no int,
  dept_no char(4),
  from_date date,
  to_date date,
  primary key(emp_no, dept_no)
);

create table salaries (
  emp_no int,
  salary int,
  from_date date,
  to_date date,
  primary key(emp_no, from_date)
);
```
