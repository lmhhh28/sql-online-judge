create table dept_emp(emp_no int, dept_no char(4), from_date date, to_date date, primary key(emp_no, dept_no));
insert into dept_emp values
  (10002, 'd001', '2020-01-01', '9999-01-01'),
  (10001, 'd001', '2020-01-01', '9999-01-01'),
  (10006, 'd002', '2020-01-01', '9999-01-01'),
  (10005, 'd003', '2020-01-01', '9999-01-01');
