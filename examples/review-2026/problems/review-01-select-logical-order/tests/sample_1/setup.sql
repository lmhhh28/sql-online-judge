create table orders(order_id int primary key, customer_id int, status text, amount numeric(10,2), created_at date);
insert into orders values
  (1, 101, 'paid', 80.00, '2026-01-05'),
  (2, 101, 'paid', 30.00, '2026-01-10'),
  (3, 102, 'paid', 140.00, '2026-02-01'),
  (4, 103, 'cancelled', 500.00, '2026-02-02'),
  (5, 104, 'paid', 90.00, '2026-03-01'),
  (6, 105, 'paid', 200.00, '2025-12-31'),
  (7, 106, 'paid', 160.00, '2026-03-05');
