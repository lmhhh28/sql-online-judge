create table acid_cases(case_id int primary key, clue text);
insert into acid_cases values (1, 'all_or_nothing'), (2, 'committed_survives'), (3, 'concurrent_hidden'), (4, 'valid_state');
