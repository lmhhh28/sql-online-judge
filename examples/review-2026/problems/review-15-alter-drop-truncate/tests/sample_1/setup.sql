create table stage_orders(order_id int primary key, amount int not null);
insert into stage_orders values (1, 10), (2, 20);
create table obsolete_cache(id int);
insert into obsolete_cache values (1);
