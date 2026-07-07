create table emp_bonus(emp_no int not null, received timestamp not null, btype smallint not null);
create table salaries(emp_no int not null, salary int not null, from_date date not null, to_date date not null, primary key(emp_no, from_date));
insert into salaries values
  (10001, 60000, '2024-01-01', '9999-01-01'),
  (10002, 50000, '2024-01-01', '9999-01-01'),
  (10001, 55000, '2023-01-01', '2023-12-31');
insert into emp_bonus values (10001, '2026-01-01', 1);
