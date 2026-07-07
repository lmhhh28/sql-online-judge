create table departments(dept_no char(4) primary key, dept_name text);
create table dept_emp(emp_no int, dept_no char(4), from_date date, to_date date, primary key(emp_no, dept_no));
create table salaries(emp_no int, salary int, from_date date, to_date date, primary key(emp_no, from_date));
insert into departments values ('x001', 'X');
insert into dept_emp values (20, 'x001', '2020-01-01', '9999-01-01'), (21, 'x001', '2020-01-01', '9999-01-01');
insert into salaries values
  (20, 1, '2020-01-01', '9999-01-01'),
  (21, 1, '2020-01-01', '2021-01-01'), (21, 2, '2021-01-01', '9999-01-01');
