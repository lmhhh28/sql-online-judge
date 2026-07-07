create table orders(order_id int primary key, customer_id int, status text, amount numeric(10,2), created_at date);
insert into orders values
  (1, 8, 'paid', 75.00, '2026-04-01'),
  (2, 8, 'paid', 25.00, '2026-04-02'),
  (3, 9, 'paid', 100.00, '2026-04-03'),
  (4, 7, 'paid', 100.00, '2026-04-04'),
  (5, 6, 'paid', 99.99, '2026-04-05');
