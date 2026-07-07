select emp_no, salary
from salaries
where to_date = date '9999-01-01'
order by emp_no;
