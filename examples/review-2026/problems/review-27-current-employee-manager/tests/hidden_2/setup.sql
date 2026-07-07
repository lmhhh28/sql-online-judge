create table dept_emp(emp_no int, dept_no char(4), from_date date, to_date date, primary key(emp_no, dept_no));
create table dept_manager(dept_no char(4), emp_no int, from_date date, to_date date, primary key(emp_no, dept_no));
insert into dept_emp values (10, 'x001', '2020-01-01', '9999-01-01'), (11, 'x001', '2020-01-01', '9999-01-01');
insert into dept_manager values ('x001', 12, '2020-01-01', '9999-01-01');
