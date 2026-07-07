create table employees(emp_no int primary key, birth_date date, first_name text, last_name text, gender char(1), hire_date date);
create table salaries(emp_no int, salary int, from_date date, to_date date, primary key(emp_no, from_date));
insert into employees values (9, '1990-01-01', 'Z', 'Z', 'M', '2010-01-01');
insert into salaries values (9, 999, '2010-01-01', '9999-01-01');
