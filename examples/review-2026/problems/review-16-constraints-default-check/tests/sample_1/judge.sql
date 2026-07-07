select item, ok
from (
  values
    ('table_exists', to_regclass('course_registrations') is not null),
    ('credits_default', exists (select 1 from information_schema.columns where table_name = 'course_registrations' and column_name = 'credits' and column_default like '%3%')),
    ('not_null_count', (select count(*) from information_schema.columns where table_name = 'course_registrations' and is_nullable = 'NO') >= 4),
    ('unique_exists', exists (select 1 from information_schema.table_constraints where table_name = 'course_registrations' and constraint_type = 'UNIQUE')),
    ('check_exists', exists (select 1 from information_schema.table_constraints where table_name = 'course_registrations' and constraint_type = 'CHECK'))
) as checks(item, ok)
order by item;
