select item, value
from (
  select 'columns' as item, string_agg(column_name, ',' order by ordinal_position) as value
  from information_schema.columns
  where table_name = 'actor_name_view'
  union all
  select 'rows', string_agg(first_name_v || ':' || last_name_v, ',' order by first_name_v, last_name_v)
  from actor_name_view
) x
order by item;
