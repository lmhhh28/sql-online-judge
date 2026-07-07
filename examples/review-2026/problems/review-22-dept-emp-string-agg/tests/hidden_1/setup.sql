create table dept_emp(emp_no int, dept_no char(4), from_date date, to_date date, primary key(emp_no, dept_no));
insert into dept_emp values
  (1, 'a001', '2020-01-01', '9999-01-01'),
  (3, 'a001', '2020-01-01', '9999-01-01'),
  (2, 'a001', '2020-01-01', '9999-01-01'),
  (9, 'b001', '2020-01-01', '9999-01-01');
