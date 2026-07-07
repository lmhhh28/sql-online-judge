create table users (
  user_id integer primary key,
  name text not null,
  total_spend numeric not null
);

insert into users(user_id, name, total_spend) values
  (1, 'Ada', 1200),
  (2, 'Ben', 999),
  (3, 'Cara', 2500);

