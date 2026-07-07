create table accounts(account_id int primary key, balance int not null);
create table audit_logs(log_id serial primary key, message text not null);
insert into accounts values (1, 500), (2, 700), (3, 900);
