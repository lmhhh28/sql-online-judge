```sql
create table orders (
  order_id int primary key,
  customer_id int,
  status text,
  amount numeric(10,2),
  created_at date
);
```
