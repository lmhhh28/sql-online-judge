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

insert into s(sno, sname, status, city) values
  ('S1', '精益', 20, '天津'),
  ('S2', '盛锡', 10, '北京'),
  ('S3', '东方红', 30, '北京'),
  ('S4', '丰泰盛', 20, '天津'),
  ('S5', '为民', 30, '上海');

insert into p(pno, pname, color, weight) values
  ('P1', '螺母', '红', 12),
  ('P2', '螺栓', '绿', 17),
  ('P3', '螺丝刀', '蓝', 14),
  ('P4', '螺丝刀', '红', 14),
  ('P5', '凸轮', '蓝', 40),
  ('P6', '齿轮', '红', 30);

insert into j(jno, jname, city) values
  ('J1', '三建', '北京'),
  ('J2', '一汽', '长春'),
  ('J3', '弹簧厂', '天津'),
  ('J4', '造船厂', '天津'),
  ('J5', '机车厂', '唐山'),
  ('J6', '无线电厂', '常州'),
  ('J7', '半导体厂', '南京');

insert into spj(sno, pno, jno, qty) values
  ('S1', 'P1', 'J1', 200),
  ('S1', 'P1', 'J3', 100),
  ('S1', 'P1', 'J4', 700),
  ('S1', 'P2', 'J2', 100),
  ('S2', 'P3', 'J1', 400),
  ('S2', 'P3', 'J2', 200),
  ('S2', 'P3', 'J4', 500),
  ('S2', 'P3', 'J5', 400),
  ('S2', 'P5', 'J1', 400),
  ('S2', 'P5', 'J2', 100),
  ('S3', 'P1', 'J1', 200),
  ('S3', 'P3', 'J1', 200),
  ('S4', 'P5', 'J1', 100),
  ('S4', 'P6', 'J3', 300),
  ('S4', 'P6', 'J4', 200),
  ('S5', 'P2', 'J4', 100),
  ('S5', 'P3', 'J1', 200),
  ('S5', 'P6', 'J2', 200),
  ('S5', 'P6', 'J4', 500);

delete from spj where sno = 'S1' and pno = 'P1' and jno = 'J3';
