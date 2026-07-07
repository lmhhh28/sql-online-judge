create table departments(dept_no text primary key, dept_name text);
create table employees(emp_no int primary key, name text, dept_no text references departments(dept_no), active boolean);
insert into departments values ('x', 'X'), ('y', 'Y');
insert into employees values (20, 'M', 'y', true), (21, 'N', 'x', true);
