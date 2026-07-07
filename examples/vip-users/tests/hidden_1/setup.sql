create table users (
  user_id integer primary key,
  name text not null,
  total_spend numeric not null
);

insert into users(user_id, name, total_spend) values
  (10, 'Ming', 1000),
  (11, 'Nora', 1000.01),
  (12, 'Owen', 100),
  (13, 'Pia', 999.99);

