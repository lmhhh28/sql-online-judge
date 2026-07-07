create table employees (
  emp_no integer primary key,
  name text not null,
  salary integer not null
);

insert into employees(emp_no, name, salary) values
  (1, 'Dana', 10000),
  (2, 'Evan', 10001),
  (3, 'Faye', 20000),
  (4, 'Gina', 5000);

