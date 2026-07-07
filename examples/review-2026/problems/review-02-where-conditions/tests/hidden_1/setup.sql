create table products(product_id int primary key, name text, category text, price numeric(10,2), stock int, discontinued boolean);
insert into products values
  (10, 'A', 'book', 80.00, 1, false),
  (11, 'B', 'book', 80.01, 1, false),
  (12, 'C', 'book', 10.00, 0, false),
  (13, 'D', 'book', 10.00, 4, true),
  (14, 'E', 'book', 0.01, 4, false);
