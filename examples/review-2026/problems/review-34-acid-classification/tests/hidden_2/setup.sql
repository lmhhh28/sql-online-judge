create table acid_cases(case_id int primary key, clue text);
insert into acid_cases values (20, 'concurrent_hidden'), (21, 'committed_survives'), (22, 'all_or_nothing');
