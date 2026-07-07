create table dept_manager(dept_no char(4), emp_no int, from_date date, to_date date, primary key(emp_no, dept_no));
create table salaries(emp_no int, salary int, from_date date, to_date date, primary key(emp_no, from_date));
insert into dept_manager values ('x001', 9, '2020-01-01', '2021-01-01'), ('x001', 10, '2021-01-01', '9999-01-01');
insert into salaries values (9, 90, '2020-01-01', '9999-01-01'), (10, 100, '2021-01-01', '9999-01-01');
