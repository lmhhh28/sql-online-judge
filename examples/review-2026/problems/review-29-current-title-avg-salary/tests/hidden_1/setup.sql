create table salaries(emp_no int, salary int, from_date date, to_date date, primary key(emp_no, from_date));
create table titles(emp_no int, title text, from_date date, to_date date);
insert into salaries values (10, 10, '2020-01-01', '9999-01-01'), (11, 20, '2020-01-01', '9999-01-01'), (12, 30, '2020-01-01', '9999-01-01');
insert into titles values (10, 'A', '2020-01-01', '9999-01-01'), (11, 'B', '2020-01-01', '9999-01-01'), (12, 'B', '2020-01-01', '9999-01-01');
