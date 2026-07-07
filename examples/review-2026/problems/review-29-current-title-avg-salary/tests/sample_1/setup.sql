create table salaries(emp_no int, salary int, from_date date, to_date date, primary key(emp_no, from_date));
create table titles(emp_no int, title text, from_date date, to_date date);
insert into salaries values (1, 100, '2020-01-01', '9999-01-01'), (2, 200, '2020-01-01', '9999-01-01'), (3, 500, '2020-01-01', '2021-01-01'), (3, 600, '2021-01-01', '9999-01-01');
insert into titles values (1, 'Engineer', '2020-01-01', '9999-01-01'), (2, 'Engineer', '2020-01-01', '9999-01-01'), (3, 'Staff', '2020-01-01', '2021-01-01');
