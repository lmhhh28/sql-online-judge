```sql
create table actor (
  actor_id smallint primary key,
  first_name text not null,
  last_name text not null,
  last_update timestamp not null default current_timestamp
);
```
