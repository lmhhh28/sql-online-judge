create table er_items(item_id int primary key, item_type text);
insert into er_items values (1, 'strong_entity'), (2, 'one_to_many'), (3, 'many_to_many'), (4, 'multivalued_attribute');
