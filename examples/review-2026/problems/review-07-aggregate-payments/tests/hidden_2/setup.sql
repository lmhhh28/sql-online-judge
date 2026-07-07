create table payments(payment_id int primary key, status text, amount numeric(10,2));
insert into payments values
  (1, 'failed', 99.00),
  (2, 'success', 100.00),
  (3, 'success', 50.00),
  (4, 'success', 25.00);
