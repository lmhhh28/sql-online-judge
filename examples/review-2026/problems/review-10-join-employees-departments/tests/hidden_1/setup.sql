create table departments(dept_no text primary key, dept_name text);
create table employees(emp_no int primary key, name text, dept_no text references departments(dept_no), active boolean);
insert into departments values ('a', 'A部'), ('b', 'B部'), ('c', 'C部');
insert into employees values (10, 'T', 'a', true), (11, 'U', 'c', true), (12, 'V', 'b', false);
