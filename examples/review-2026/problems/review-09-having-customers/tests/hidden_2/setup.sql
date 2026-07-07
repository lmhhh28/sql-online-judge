create table orders(order_id int primary key, customer_id int, status text, amount numeric(10,2));
insert into orders values
  (1, 20, 'paid', 20.00),
  (2, 20, 'paid', 30.00),
  (3, 20, 'paid', 50.00),
  (4, 21, 'paid', 99.99),
  (5, 21, 'paid', 0.01);
