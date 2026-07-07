```sql
create table emp_bonus (
  emp_no int not null,
  received timestamp not null,
  btype smallint not null
);

create table salaries (
  emp_no int not null,
  salary int not null,
  from_date date not null,
  to_date date not null,
  primary key(emp_no, from_date)
);
```
