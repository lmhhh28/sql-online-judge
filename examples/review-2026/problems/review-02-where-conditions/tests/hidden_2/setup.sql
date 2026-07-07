create table products(product_id int primary key, name text, category text, price numeric(10,2), stock int, discontinued boolean);
insert into products values
  (21, '网络工程', 'book', 79.99, 9, false),
  (22, '鼠标', 'device', 20.00, 1, false),
  (23, '编译原理', 'book', 81.00, 9, false),
  (24, '数据结构', 'book', 45.00, 1, false);
