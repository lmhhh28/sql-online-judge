```sql
create table dept_manager (
  dept_no char(4),
  emp_no int,
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
