```sql
create table nf_cases (
  case_id int primary key,
  all_attrs_atomic boolean,
  has_partial_dependency boolean,
  has_transitive_dependency boolean
);
```
