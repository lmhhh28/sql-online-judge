create table nf_cases(case_id int primary key, all_attrs_atomic boolean, has_partial_dependency boolean, has_transitive_dependency boolean);
insert into nf_cases values
  (1, false, false, false),
  (2, true, true, false),
  (3, true, false, true),
  (4, true, false, false);
