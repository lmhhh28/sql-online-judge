create table employees (
  emp_no integer primary key,
  name text not null,
  salary integer not null
);

insert into employees(emp_no, name, salary) values
  (1001, 'Alice', 12000),
  (1002, 'Bob', 9000),
  (1003, 'Cindy', 15000);

