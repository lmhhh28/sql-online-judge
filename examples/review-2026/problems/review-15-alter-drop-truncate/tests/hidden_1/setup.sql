create table stage_orders(order_id int primary key, amount int not null);
insert into stage_orders values (10, 100);
create table obsolete_cache(id int);
