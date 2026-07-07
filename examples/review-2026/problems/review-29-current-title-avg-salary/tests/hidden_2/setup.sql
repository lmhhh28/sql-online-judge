create table salaries(emp_no int, salary int, from_date date, to_date date, primary key(emp_no, from_date));
create table titles(emp_no int, title text, from_date date, to_date date);
insert into salaries values (20, 94409, '2020-01-01', '9999-01-01'), (21, 72527, '2020-01-01', '9999-01-01');
insert into titles values (20, 'Engineer', '2020-01-01', '9999-01-01'), (21, 'Staff', '2020-01-01', '9999-01-01');
