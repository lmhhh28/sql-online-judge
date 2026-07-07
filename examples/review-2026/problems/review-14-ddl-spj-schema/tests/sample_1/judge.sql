select check_name, ok
from (
  values
    ('j_pk', exists (select 1 from information_schema.table_constraints where table_name = 'j' and constraint_type = 'PRIMARY KEY')),
    ('p_pk', exists (select 1 from information_schema.table_constraints where table_name = 'p' and constraint_type = 'PRIMARY KEY')),
    ('s_pk', exists (select 1 from information_schema.table_constraints where table_name = 's' and constraint_type = 'PRIMARY KEY')),
    ('spj_fk_count', (select count(*) from information_schema.table_constraints where table_name = 'spj' and constraint_type = 'FOREIGN KEY') = 3),
    ('spj_pk', exists (select 1 from information_schema.table_constraints where table_name = 'spj' and constraint_type = 'PRIMARY KEY'))
) as checks(check_name, ok)
order by check_name;
