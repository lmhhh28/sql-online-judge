create table emp_bonus(emp_no int not null, received timestamp not null, btype smallint not null);
create table salaries(emp_no int not null, salary int not null, from_date date not null, to_date date not null, primary key(emp_no, from_date));
insert into salaries values
  (1, 10001, '2026-01-01', '9999-01-01'),
  (2, 10005, '2026-01-01', '9999-01-01'),
  (3, 77777, '2026-01-01', '2026-06-30');
insert into emp_bonus values (1, '2026-02-01', 1), (1, '2026-03-01', 2), (3, '2026-04-01', 1);
