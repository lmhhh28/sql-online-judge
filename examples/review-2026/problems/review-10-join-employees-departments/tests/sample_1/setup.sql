create table departments(dept_no text primary key, dept_name text);
create table employees(emp_no int primary key, name text, dept_no text references departments(dept_no), active boolean);
insert into departments values ('d001', '研发'), ('d002', '财务');
insert into employees values (1, 'Alice', 'd001', true), (2, 'Bob', 'd002', false), (3, 'Cindy', 'd002', true);
