create table payments(payment_id int primary key, status text, amount numeric(10,2));
insert into payments values
  (1, 'success', 1.00),
  (2, 'success', 2.00),
  (3, 'success', 2.00);
