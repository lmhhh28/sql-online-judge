create table employees(emp_no int primary key, birth_date date, first_name text, last_name text, gender char(1), hire_date date);
create table salaries(emp_no int, salary int, from_date date, to_date date, primary key(emp_no, from_date));
insert into employees values
  (10001, '1953-09-02', 'Georgi', 'Facello', 'M', '1986-06-26'),
  (10002, '1964-06-02', 'Bezalel', 'Simmel', 'F', '1985-11-21');
insert into salaries values
  (10001, 60117, '1986-06-26', '1987-06-26'),
  (10001, 62102, '1987-06-26', '9999-01-01'),
  (10002, 65828, '1985-11-21', '9999-01-01');
