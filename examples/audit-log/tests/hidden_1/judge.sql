select count(*) as matching_columns
from information_schema.columns
where table_schema = 'public'
  and table_name = 'audit_log'
  and (
    (column_name = 'id' and data_type = 'integer' and is_nullable = 'NO')
    or (column_name = 'action' and data_type = 'text' and is_nullable = 'NO')
    or (column_name = 'created_at' and data_type = 'timestamp without time zone' and is_nullable = 'NO')
  );

