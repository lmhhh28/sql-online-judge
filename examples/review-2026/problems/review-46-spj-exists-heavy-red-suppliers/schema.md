```sql
create table s (
  sno text primary key,
  sname text not null,
  status integer not null,
  city text not null
);

create table p (
  pno text primary key,
  pname text not null,
  color text not null,
  weight integer not null
);

create table j (
  jno text primary key,
  jname text not null,
  city text not null
);

create table spj (
  sno text not null references s(sno) on delete cascade,
  pno text not null references p(pno) on delete cascade,
  jno text not null references j(jno) on delete cascade,
  qty integer not null,
  primary key (sno, pno, jno)
);
```
