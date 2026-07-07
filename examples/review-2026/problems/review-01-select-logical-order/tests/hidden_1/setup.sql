create table orders(order_id int primary key, customer_id int, status text, amount numeric(10,2), created_at date);
insert into orders values
  (1, 1, 'paid', 40.00, '2026-01-01'),
  (2, 1, 'paid', 60.00, '2026-01-02'),
  (3, 2, 'paid', 250.00, '2026-01-03'),
  (4, 3, 'paid', 120.00, '2026-01-04'),
  (5, 4, 'paid', 119.00, '2026-01-05'),
  (6, 5, 'refunded', 900.00, '2026-01-06'),
  (7, 6, 'paid', 100.00, '2024-01-01');
