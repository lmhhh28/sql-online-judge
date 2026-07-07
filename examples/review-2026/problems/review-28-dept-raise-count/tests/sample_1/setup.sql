create table departments(dept_no char(4) primary key, dept_name text);
create table dept_emp(emp_no int, dept_no char(4), from_date date, to_date date, primary key(emp_no, dept_no));
create table salaries(emp_no int, salary int, from_date date, to_date date, primary key(emp_no, from_date));
insert into departments values ('d001', 'Marketing'), ('d002', 'Finance');
insert into dept_emp values (1, 'd001', '2020-01-01', '9999-01-01'), (2, 'd001', '2020-01-01', '9999-01-01'), (3, 'd002', '2020-01-01', '9999-01-01');
insert into salaries values
  (1, 10, '2020-01-01', '2021-01-01'), (1, 20, '2021-01-01', '9999-01-01'),
  (2, 10, '2020-01-01', '2021-01-01'), (2, 20, '2021-01-01', '2022-01-01'), (2, 30, '2022-01-01', '9999-01-01'),
  (3, 50, '2020-01-01', '9999-01-01');
