create table payments(payment_id int primary key, status text, amount numeric(10,2));
insert into payments values
  (1, 'success', 10.00),
  (2, 'failed', 100.00),
  (3, 'success', 20.50),
  (4, 'success', 30.25);
