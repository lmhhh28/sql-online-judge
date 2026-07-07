select item, value
from (
  select 'has_status' as item, exists (
    select 1 from information_schema.columns
    where table_name = 'stage_orders' and column_name = 'status' and is_nullable = 'NO'
  )::text as value
  union all
  select 'obsolete_exists', (to_regclass('obsolete_cache') is not null)::text
  union all
  select 'rows', string_agg(order_id::text || ':' || amount::text || ':' || status, ',' order by order_id)
  from stage_orders
) x
order by item;
