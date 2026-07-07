create table orders(order_id int primary key, customer_id int, status text, amount numeric(10,2));
insert into orders values
  (1, 1, 'paid', 40.00),
  (2, 1, 'paid', 60.00),
  (3, 2, 'paid', 120.00),
  (4, 2, 'cancelled', 10.00),
  (5, 3, 'paid', 50.00),
  (6, 3, 'paid', 49.99);
