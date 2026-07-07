select privilege_type
from information_schema.table_privileges
where table_schema = 'public'
  and table_name = 'reports'
  and grantee = 'PUBLIC'
order by privilege_type;
