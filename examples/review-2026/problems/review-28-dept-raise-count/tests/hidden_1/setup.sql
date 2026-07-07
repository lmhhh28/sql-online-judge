create table departments(dept_no char(4) primary key, dept_name text);
create table dept_emp(emp_no int, dept_no char(4), from_date date, to_date date, primary key(emp_no, dept_no));
create table salaries(emp_no int, salary int, from_date date, to_date date, primary key(emp_no, from_date));
insert into departments values ('a001', 'A'), ('a002', 'B');
insert into dept_emp values (10, 'a001', '2020-01-01', '9999-01-01'), (11, 'a002', '2020-01-01', '9999-01-01');
insert into salaries values
  (10, 1, '2020-01-01', '2021-01-01'), (10, 2, '2021-01-01', '2022-01-01'), (10, 3, '2022-01-01', '9999-01-01'),
  (11, 9, '2020-01-01', '9999-01-01');
