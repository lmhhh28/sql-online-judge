create table emp_bonus(emp_no int not null, received timestamp not null, btype smallint not null);
create table salaries(emp_no int not null, salary int not null, from_date date not null, to_date date not null, primary key(emp_no, from_date));
insert into salaries values
  (10, 80000, '2026-01-01', '9999-01-01'),
  (11, 90000, '2026-01-01', '9999-01-01');
insert into emp_bonus values (11, '2026-05-01', 1);
