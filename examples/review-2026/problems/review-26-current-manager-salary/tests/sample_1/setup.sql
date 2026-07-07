create table dept_manager(dept_no char(4), emp_no int, from_date date, to_date date, primary key(emp_no, dept_no));
create table salaries(emp_no int, salary int, from_date date, to_date date, primary key(emp_no, from_date));
insert into dept_manager values ('d001', 10002, '2020-01-01', '9999-01-01'), ('d002', 10006, '2020-01-01', '9999-01-01'), ('d003', 10007, '2020-01-01', '2021-01-01');
insert into salaries values (10002, 72527, '2020-01-01', '9999-01-01'), (10006, 43311, '2020-01-01', '9999-01-01'), (10007, 1, '2020-01-01', '9999-01-01');
