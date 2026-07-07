create table actor(actor_id smallint primary key, first_name text not null, last_name text not null, last_update timestamp not null default current_timestamp);
insert into actor(actor_id, first_name, last_name) values (1, 'PENELOPE', 'GUINESS'), (2, 'NICK', 'WAHLBERG');
