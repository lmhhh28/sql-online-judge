create table orders(order_id int primary key, customer_id int, status text, amount numeric(10,2));
insert into orders values
  (1, 10, 'paid', 100.00),
  (2, 10, 'paid', 1.00),
  (3, 11, 'paid', 50.00),
  (4, 11, 'paid', 50.00);
