create table nf_cases(case_id int primary key, all_attrs_atomic boolean, has_partial_dependency boolean, has_transitive_dependency boolean);
insert into nf_cases values
  (20, false, true, true),
  (21, true, false, true),
  (22, true, false, false);
