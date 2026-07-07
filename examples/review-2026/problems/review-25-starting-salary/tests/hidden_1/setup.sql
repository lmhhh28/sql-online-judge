create table employees(emp_no int primary key, birth_date date, first_name text, last_name text, gender char(1), hire_date date);
create table salaries(emp_no int, salary int, from_date date, to_date date, primary key(emp_no, from_date));
insert into employees values
  (1, '1990-01-01', 'A', 'A', 'M', '2020-01-01'),
  (2, '1990-01-01', 'B', 'B', 'F', '2021-01-01'),
  (3, '1990-01-01', 'C', 'C', 'F', '2022-01-01');
insert into salaries values
  (1, 10, '2020-01-01', '9999-01-01'),
  (2, 20, '2021-01-01', '9999-01-01'),
  (3, 30, '2021-01-01', '2022-01-01'),
  (3, 40, '2022-01-01', '9999-01-01');
