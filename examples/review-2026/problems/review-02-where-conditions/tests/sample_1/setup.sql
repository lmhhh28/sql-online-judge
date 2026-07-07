create table products(product_id int primary key, name text, category text, price numeric(10,2), stock int, discontinued boolean);
insert into products values
  (1, '数据库系统', 'book', 59.90, 5, false),
  (2, '键盘', 'device', 79.00, 8, false),
  (3, 'SQL 习题集', 'book', 88.00, 3, false),
  (4, '离散数学', 'book', 66.00, 0, false),
  (5, '操作系统', 'book', 72.50, 2, false),
  (6, '旧教材', 'book', 30.00, 10, true);
