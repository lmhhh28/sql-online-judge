select indexname, has_customer_date
from (
  select indexname,
         (indexdef like '%(customer_id, order_date)%') as has_customer_date
  from pg_indexes
  where schemaname = 'public' and tablename = 'orders' and indexname = 'idx_orders_customer_date'
) x
order by indexname;
