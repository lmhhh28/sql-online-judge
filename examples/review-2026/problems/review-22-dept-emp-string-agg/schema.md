```sql
create table dept_emp (
  emp_no int,
  dept_no char(4),
  from_date date,
  to_date date,
  primary key(emp_no, dept_no)
);
```
