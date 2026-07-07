create table dept_emp(emp_no int, dept_no char(4), from_date date, to_date date, primary key(emp_no, dept_no));
insert into dept_emp values
  (10, 'd010', '2020-01-01', '9999-01-01'),
  (11, 'd011', '2020-01-01', '9999-01-01'),
  (12, 'd010', '2020-01-01', '9999-01-01');
