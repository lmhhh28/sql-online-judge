```sql
create table salaries (
  emp_no int,
  salary int,
  from_date date,
  to_date date,
  primary key(emp_no, from_date)
);

create table titles (
  emp_no int,
  title text,
  from_date date,
  to_date date
);
```
