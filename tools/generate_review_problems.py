from __future__ import annotations

import csv
import ctypes
import errno
import io
import os
import re
import shutil
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

import psycopg
import yaml
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "examples" / "review-2026"
NULL_MARKER = r"\N"
USER_OUTPUT_SENTINEL = "__USER_OUTPUT__"
TEMP_DATABASE_PATTERN = re.compile(r"oj_pkg_[0-9a-f]{20}\Z")
DATABASE_CONNECT_TIMEOUT_SECONDS = 5
FORBIDDEN_LIBPQ_ENVIRONMENT = ("PGSERVICE", "PGSERVICEFILE", "PGOPTIONS")


class DatabaseCreationOutcomeUnknown(RuntimeError):
    """CREATE DATABASE was sent, but no definitive server result was received."""


FOCUSED_REVIEW_SLUGS = [
    "review-09-having-customers",
    "review-10-join-employees-departments",
    "review-11-subquery-above-dept-average",
    "review-13-dml-bonus-salary",
    "review-17-view-actor-name",
    "review-24-latest-hire-employee",
    "review-25-starting-salary",
    "review-26-current-manager-salary",
    "review-27-current-employee-manager",
    "review-29-current-title-avg-salary",
    "review-30-spj-red-suppliers-j1",
    "review-31-spj-no-tianjin-red-projects",
    "review-32-spj-division-s1-parts",
    "review-33-spj-sanjian-view",
    "review-38-lab-zhang-teacher-experiments",
    "review-39-lab-senior-teachers-without-records",
    "review-40-lab-teachers-all-category-c",
    "review-41-lab-students-at-least-two",
    "review-42-lab-overtime-score-update",
    "review-43-lab-lowest-score-records",
    "review-44-lab-teacher-student-count",
    "review-45-lab-student-summary-view",
    "review-46-spj-exists-heavy-red-suppliers",
    "review-47-spj-in-red-part-suppliers",
    "review-48-lab-not-exists-no-b-experiment",
    "review-49-spj-not-in-no-tianjin-supplier-projects",
    "review-50-lab-division-students-all-c",
]


@dataclass(frozen=True)
class TestSpec:
    name: str
    setup_sql: str


@dataclass(frozen=True)
class ProblemSpec:
    slug: str
    title: str
    difficulty: str
    tags: list[str]
    statement: str
    schema: str
    solution_sql: str
    tests: list[TestSpec]
    judge_sql: str = USER_OUTPUT_SENTINEL
    ordered: bool = True
    checker: str = "result"
    time_limit_ms: int = 3000


def rows_to_csv(columns: Iterable[str], rows: Iterable[Iterable[object]]) -> str:
    stream = io.StringIO()
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(list(columns))
    for row in rows:
        writer.writerow([NULL_MARKER if cell is None else cell for cell in row])
    return stream.getvalue()


def psycopg_url(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


def admin_conninfo(database: str | None = None) -> str:
    unsafe_environment = [
        variable for variable in FORBIDDEN_LIBPQ_ENVIRONMENT if os.environ.get(variable)
    ]
    if unsafe_environment:
        raise RuntimeError(
            "Unsafe libpq environment variables are set: "
            + ", ".join(unsafe_environment)
        )
    url = os.environ.get("ADMIN_DATABASE_URL", "postgresql:///postgres?host=/tmp")
    params = conninfo_to_dict(psycopg_url(url))
    if any(parameter in params for parameter in ("service", "servicefile", "options")):
        raise RuntimeError(
            "ADMIN_DATABASE_URL must not use service, servicefile, or startup options"
        )
    if database is not None:
        params["dbname"] = database
    if not params.get("dbname"):
        raise RuntimeError("ADMIN_DATABASE_URL must include a database name")
    params["connect_timeout"] = str(DATABASE_CONNECT_TIMEOUT_SECONDS)
    return make_conninfo(**params)


def fetch_csv(cursor) -> str:
    columns = [desc.name for desc in cursor.description or []]
    rows = cursor.fetchall() if cursor.description else []
    return rows_to_csv(columns, rows)


def _split_top_level_commas(text: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    cur: list[str] = []
    for ch in text:
        if ch == "(":
            depth += 1
            cur.append(ch)
        elif ch == ")":
            depth -= 1
            cur.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    if cur:
        parts.append("".join(cur))
    return parts


def derive_schema_ddl(setup_sql: str) -> str:
    statements: list[str] = []
    pattern = re.compile(
        r"create\s+table\s+(?:if\s+not\s+exists\s+)?([a-zA-Z_]\w*)\s*\(", re.IGNORECASE
    )
    for m in pattern.finditer(setup_sql):
        table_name = m.group(1)
        start = m.end() - 1
        depth = 0
        end = None
        for i in range(start, len(setup_sql)):
            if setup_sql[i] == "(":
                depth += 1
            elif setup_sql[i] == ")":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        if end is None:
            continue
        inner = setup_sql[start + 1 : end]
        cols = [c.strip() for c in _split_top_level_commas(inner) if c.strip()]
        if not cols:
            continue
        statements.append(
            f"create table {table_name} (\n  " + ",\n  ".join(cols) + "\n);"
        )
    if not statements:
        return ""
    return "```sql\n" + "\n\n".join(statements) + "\n```"


def create_database(db_name: str) -> None:
    if TEMP_DATABASE_PATTERN.fullmatch(db_name) is None:
        raise ValueError(
            "Refusing to create a database outside the package-generator namespace"
        )
    connection_info = admin_conninfo()
    statement = sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(
        sql.Identifier(db_name)
    )
    statement_started = False
    statement_returned = False
    try:
        with psycopg.connect(connection_info, autocommit=True) as conn:
            statement_started = True
            conn.execute(statement)
            statement_returned = True
    except BaseException as exc:
        # Before execute, no CREATE was sent. A psycopg error carrying SQLSTATE
        # is also a definitive server rejection, so the database was not
        # created. Only a post-send transport/protocol failure is ambiguous.
        if not statement_started or (
            not statement_returned
            and isinstance(exc, psycopg.Error)
            and exc.sqlstate is not None
        ):
            raise
        raise DatabaseCreationOutcomeUnknown(
            f"CREATE DATABASE {db_name} may have succeeded before the connection failed"
        ) from exc


def drop_database(db_name: str) -> None:
    if TEMP_DATABASE_PATTERN.fullmatch(db_name) is None:
        raise ValueError(
            "Refusing to drop a database outside the package-generator namespace"
        )
    with psycopg.connect(admin_conninfo(), autocommit=True) as conn:
        conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s",
            (db_name,),
        )
        conn.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                sql.Identifier(db_name)
            )
        )


def expected_output(problem: ProblemSpec, test: TestSpec) -> str:
    db_name = f"oj_pkg_{uuid.uuid4().hex[:20]}"
    generation_error: BaseException | None = None
    cleanup_database = False
    try:
        # create_database distinguishes a definitive server rejection from a
        # transport failure whose CREATE result is unknown. Only the latter
        # enters the compensating DROP IF EXISTS path when creation raises.
        create_database(db_name)
        cleanup_database = True
        with psycopg.connect(admin_conninfo(db_name), autocommit=True) as conn:
            if test.setup_sql.strip():
                conn.execute(test.setup_sql)
            cursor = conn.execute(problem.solution_sql)
            if problem.judge_sql.strip() == USER_OUTPUT_SENTINEL:
                result_sets = []
                if cursor.description:
                    result_sets.append(fetch_csv(cursor))
                while cursor.nextset():
                    if cursor.description:
                        result_sets.append(fetch_csv(cursor))
                if len(result_sets) != 1:
                    raise RuntimeError(
                        f"{problem.slug}/{test.name} solution must return exactly one result set"
                    )
                return result_sets[0]

            cursor = conn.execute(problem.judge_sql)
            return fetch_csv(cursor)
    except BaseException as exc:
        generation_error = exc
        if isinstance(exc, DatabaseCreationOutcomeUnknown):
            cleanup_database = True
        raise
    finally:
        if cleanup_database:
            try:
                drop_database(db_name)
            except Exception as cleanup_error:
                if generation_error is None:
                    raise
                print(
                    f"warning: failed to drop temporary database {db_name}: {cleanup_error}",
                    file=sys.stderr,
                )


def test_name(index: int) -> str:
    return "sample_1" if index == 0 else f"hidden_{index}"


def emp_schema(*tables: str) -> str:
    return "\n\n".join(tables)


def spj_setup(extra_sql: str = "") -> str:
    # `extra_sql` is supplied only by the static problem specifications below.
    return f"""
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

{extra_sql}
"""  # nosec B608


def lab_setup(extra_sql: str = "") -> str:
    # `extra_sql` is supplied only by the static problem specifications below.
    return f"""
create table lab_teachers (
  teacher_id text primary key,
  teacher_name text not null,
  title text not null,
  phone text
);

create table students (
  student_id text primary key,
  student_name text not null,
  class_name text not null,
  major text not null
);

create table experiments (
  experiment_id text primary key,
  experiment_name text not null,
  required_minutes integer not null,
  category text not null
);

create table experiment_records (
  student_id text not null references students(student_id),
  experiment_id text not null references experiments(experiment_id),
  teacher_id text not null references lab_teachers(teacher_id),
  actual_minutes integer not null,
  score integer not null,
  primary key (student_id, experiment_id)
);

insert into lab_teachers values
  ('T1', '张sir', '高工', '13800000001'),
  ('T2', '李sir', '工程师', '13800000002'),
  ('T3', '王sir', '高工', '13800000003'),
  ('T4', '赵sir', '高工', '13800000004'),
  ('T5', '陈sir', '教授', '13800000005');

insert into students values
  ('S1', '小王', '计科2301', '计科'),
  ('S2', '小李', '计科2301', '计科'),
  ('S3', '小周', '软件2302', '软件'),
  ('S4', '小陈', '网络2301', '网络');

insert into experiments values
  ('E1', 'SQL基础', 90, 'A类'),
  ('E2', '嵌套查询', 120, 'C类'),
  ('E3', '视图设计', 100, 'C类'),
  ('E4', '事务恢复', 80, 'B类'),
  ('E5', '连接查询', 60, 'C类');

insert into experiment_records values
  ('S1', 'E1', 'T1', 90, 85),
  ('S1', 'E2', 'T1', 130, 80),
  ('S1', 'E3', 'T2', 100, 91),
  ('S1', 'E5', 'T1', 70, 88),
  ('S2', 'E2', 'T2', 118, 76),
  ('S2', 'E3', 'T2', 150, 60),
  ('S2', 'E5', 'T2', 58, 84),
  ('S3', 'E4', 'T1', 70, 70),
  ('S3', 'E2', 'T5', 140, 59),
  ('S4', 'E5', 'T5', 55, 92);

{extra_sql}
"""  # nosec B608


def mk_tests(*setups: str) -> list[TestSpec]:
    return [TestSpec(test_name(i), setup) for i, setup in enumerate(setups)]


def problem_specs() -> list[ProblemSpec]:
    specs: list[ProblemSpec] = []

    specs.append(
        ProblemSpec(
            slug="review-01-select-logical-order",
            title="SELECT 逻辑执行顺序综合查询",
            difficulty="中等",
            tags=["SELECT", "WHERE", "GROUP BY", "HAVING", "ORDER BY", "LIMIT"],
            statement="""给定订单表 `orders`，查询 2026 年以来已支付订单中，总金额不少于 100 的客户。

输出列：

| 列名 | 含义 |
| --- | --- |
| `customer_id` | 客户编号 |
| `total_amount` | 已支付订单总金额，保留两位小数 |
| `order_count` | 已支付订单数 |

按 `total_amount` 降序、`customer_id` 升序输出，最多输出前 3 行。""",
            schema="orders(order_id, customer_id, status, amount, created_at)",
            solution_sql="""
select customer_id,
       sum(amount)::numeric(10,2) as total_amount,
       count(*) as order_count
from orders
where status = 'paid'
  and created_at >= date '2026-01-01'
group by customer_id
having sum(amount) >= 100
order by total_amount desc, customer_id asc
limit 3;
""",
            tests=mk_tests(
                """
create table orders(order_id int primary key, customer_id int, status text, amount numeric(10,2), created_at date);
insert into orders values
  (1, 101, 'paid', 80.00, '2026-01-05'),
  (2, 101, 'paid', 30.00, '2026-01-10'),
  (3, 102, 'paid', 140.00, '2026-02-01'),
  (4, 103, 'cancelled', 500.00, '2026-02-02'),
  (5, 104, 'paid', 90.00, '2026-03-01'),
  (6, 105, 'paid', 200.00, '2025-12-31'),
  (7, 106, 'paid', 160.00, '2026-03-05');
""",
                """
create table orders(order_id int primary key, customer_id int, status text, amount numeric(10,2), created_at date);
insert into orders values
  (1, 1, 'paid', 40.00, '2026-01-01'),
  (2, 1, 'paid', 60.00, '2026-01-02'),
  (3, 2, 'paid', 250.00, '2026-01-03'),
  (4, 3, 'paid', 120.00, '2026-01-04'),
  (5, 4, 'paid', 119.00, '2026-01-05'),
  (6, 5, 'refunded', 900.00, '2026-01-06'),
  (7, 6, 'paid', 100.00, '2024-01-01');
""",
                """
create table orders(order_id int primary key, customer_id int, status text, amount numeric(10,2), created_at date);
insert into orders values
  (1, 8, 'paid', 75.00, '2026-04-01'),
  (2, 8, 'paid', 25.00, '2026-04-02'),
  (3, 9, 'paid', 100.00, '2026-04-03'),
  (4, 7, 'paid', 100.00, '2026-04-04'),
  (5, 6, 'paid', 99.99, '2026-04-05');
""",
            ),
        )
    )

    specs.append(
        ProblemSpec(
            slug="review-02-where-conditions",
            title="WHERE 多条件筛选",
            difficulty="简单",
            tags=["WHERE"],
            statement="""给定商品表 `products`，查询仍在售、库存大于 0、分类为 `book`、价格不超过 80 的商品。

输出 `product_id,name,price`，按 `product_id` 升序。""",
            schema="products(product_id, name, category, price, stock, discontinued)",
            solution_sql="""
select product_id, name, price::numeric(10,2) as price
from products
where category = 'book'
  and price <= 80
  and stock > 0
  and discontinued = false
order by product_id;
""",
            tests=mk_tests(
                """
create table products(product_id int primary key, name text, category text, price numeric(10,2), stock int, discontinued boolean);
insert into products values
  (1, '数据库系统', 'book', 59.90, 5, false),
  (2, '键盘', 'device', 79.00, 8, false),
  (3, 'SQL 习题集', 'book', 88.00, 3, false),
  (4, '离散数学', 'book', 66.00, 0, false),
  (5, '操作系统', 'book', 72.50, 2, false),
  (6, '旧教材', 'book', 30.00, 10, true);
""",
                """
create table products(product_id int primary key, name text, category text, price numeric(10,2), stock int, discontinued boolean);
insert into products values
  (10, 'A', 'book', 80.00, 1, false),
  (11, 'B', 'book', 80.01, 1, false),
  (12, 'C', 'book', 10.00, 0, false),
  (13, 'D', 'book', 10.00, 4, true),
  (14, 'E', 'book', 0.01, 4, false);
""",
                """
create table products(product_id int primary key, name text, category text, price numeric(10,2), stock int, discontinued boolean);
insert into products values
  (21, '网络工程', 'book', 79.99, 9, false),
  (22, '鼠标', 'device', 20.00, 1, false),
  (23, '编译原理', 'book', 81.00, 9, false),
  (24, '数据结构', 'book', 45.00, 1, false);
""",
            ),
        )
    )

    specs.append(
        ProblemSpec(
            slug="review-03-like-in-between-null",
            title="LIKE、IN、BETWEEN、IS NULL",
            difficulty="简单",
            tags=["LIKE", "IN", "BETWEEN", "IS NULL"],
            statement="""给定学生表 `students`，查询满足以下条件的学生：姓名以 `张` 开头或城市为 `武汉`/`上海`，成绩在 80 到 95 之间，且邮箱不为空。

输出 `student_id,name`，按 `student_id` 升序。""",
            schema="students(student_id, name, city, score, email)",
            solution_sql="""
select student_id, name
from students
where (name like '张%' or city in ('武汉', '上海'))
  and score between 80 and 95
  and email is not null
order by student_id;
""",
            tests=mk_tests(
                """
create table students(student_id int primary key, name text, city text, score int, email text);
insert into students values
  (1, '张三', '北京', 88, 'a@example.com'),
  (2, '李四', '武汉', 92, 'b@example.com'),
  (3, '王五', '上海', 79, 'c@example.com'),
  (4, '张敏', '广州', 95, null),
  (5, '赵六', '南京', 90, 'd@example.com');
""",
                """
create table students(student_id int primary key, name text, city text, score int, email text);
insert into students values
  (10, '张一', '成都', 80, 'z1@example.com'),
  (11, '张二', '成都', 96, 'z2@example.com'),
  (12, '陈一', '武汉', 95, 'c1@example.com'),
  (13, '陈二', '上海', 80, null),
  (14, '刘一', '上海', 85, 'l1@example.com');
""",
                """
create table students(student_id int primary key, name text, city text, score int, email text);
insert into students values
  (20, '张强', '武汉', 82, 'a@x.com'),
  (21, '周强', '武汉', 77, 'b@x.com'),
  (22, '张雨', '深圳', 94, 'c@x.com'),
  (23, '王雨', '杭州', 94, 'd@x.com');
""",
            ),
        )
    )

    specs.append(
        ProblemSpec(
            slug="review-04-distinct-courses",
            title="DISTINCT 去重",
            difficulty="简单",
            tags=["DISTINCT"],
            statement="给定选课表 `enrollments`，查询 2026 春季中至少有一名学生及格的课程编号。输出 `course_id`，去重后按课程编号升序。",
            schema="enrollments(student_id, course_id, semester, score)",
            solution_sql="""
select distinct course_id
from enrollments
where semester = '2026春' and score >= 60
order by course_id;
""",
            tests=mk_tests(
                """
create table enrollments(student_id int, course_id text, semester text, score int);
insert into enrollments values
  (1, 'DB', '2026春', 90),
  (2, 'DB', '2026春', 50),
  (3, 'OS', '2026春', 61),
  (4, 'AI', '2026秋', 99),
  (5, 'NET', '2026春', 59);
""",
                """
create table enrollments(student_id int, course_id text, semester text, score int);
insert into enrollments values
  (1, 'C', '2026春', 60),
  (2, 'A', '2026春', 100),
  (3, 'B', '2026春', 0),
  (4, 'A', '2026春', 70),
  (5, 'D', '2025秋', 90);
""",
                """
create table enrollments(student_id int, course_id text, semester text, score int);
insert into enrollments values
  (10, 'MATH', '2026春', 59),
  (11, 'MATH', '2026春', 60),
  (12, 'PHY', '2026春', 61),
  (13, 'CHEM', '2026春', 55);
""",
            ),
        )
    )

    specs.append(
        ProblemSpec(
            slug="review-05-order-by-employees",
            title="ORDER BY 多列排序",
            difficulty="简单",
            tags=["ORDER BY"],
            statement="给定员工表 `employees`，输出 `emp_no,name,dept_no,salary,hire_date`。排序规则：部门升序，工资降序，入职日期升序，员工编号升序。",
            schema="employees(emp_no, name, dept_no, salary, hire_date)",
            solution_sql="""
select emp_no, name, dept_no, salary, hire_date
from employees
order by dept_no asc, salary desc, hire_date asc, emp_no asc;
""",
            tests=mk_tests(
                """
create table employees(emp_no int primary key, name text, dept_no text, salary int, hire_date date);
insert into employees values
  (1, 'A', 'd002', 7000, '2024-01-01'),
  (2, 'B', 'd001', 8000, '2024-02-01'),
  (3, 'C', 'd001', 8000, '2023-12-01'),
  (4, 'D', 'd001', 6000, '2024-03-01');
""",
                """
create table employees(emp_no int primary key, name text, dept_no text, salary int, hire_date date);
insert into employees values
  (10, 'Tom', 'x', 1, '2020-01-01'),
  (11, 'Ann', 'x', 1, '2019-01-01'),
  (12, 'Bob', 'a', 9, '2021-01-01');
""",
                """
create table employees(emp_no int primary key, name text, dept_no text, salary int, hire_date date);
insert into employees values
  (20, 'M', 'd003', 9000, '2024-01-01'),
  (21, 'N', 'd002', 9000, '2024-01-01'),
  (22, 'O', 'd002', 9500, '2024-02-01');
""",
            ),
        )
    )

    specs.append(
        ProblemSpec(
            slug="review-06-limit-offset-articles",
            title="LIMIT OFFSET 分页",
            difficulty="简单",
            tags=["LIMIT", "OFFSET"],
            statement="给定文章表 `articles`，按 `published_at` 降序、`article_id` 降序排序，跳过前 2 条后取 3 条。输出 `article_id,title`。",
            schema="articles(article_id, title, published_at)",
            solution_sql="""
select article_id, title
from articles
order by published_at desc, article_id desc
limit 3 offset 2;
""",
            tests=mk_tests(
                """
create table articles(article_id int primary key, title text, published_at timestamp);
insert into articles values
  (1, 'A', '2026-01-01 10:00'),
  (2, 'B', '2026-01-02 10:00'),
  (3, 'C', '2026-01-03 10:00'),
  (4, 'D', '2026-01-04 10:00'),
  (5, 'E', '2026-01-05 10:00'),
  (6, 'F', '2026-01-05 10:00');
""",
                """
create table articles(article_id int primary key, title text, published_at timestamp);
insert into articles values
  (10, 'T10', '2026-06-01 00:00'),
  (11, 'T11', '2026-06-02 00:00'),
  (12, 'T12', '2026-06-03 00:00'),
  (13, 'T13', '2026-06-04 00:00');
""",
                """
create table articles(article_id int primary key, title text, published_at timestamp);
insert into articles values
  (20, 'T20', '2026-01-01 00:00'),
  (21, 'T21', '2026-01-01 00:00'),
  (22, 'T22', '2026-01-01 00:00'),
  (23, 'T23', '2026-01-02 00:00'),
  (24, 'T24', '2026-01-03 00:00');
""",
            ),
        )
    )

    specs.append(
        ProblemSpec(
            slug="review-07-aggregate-payments",
            title="聚合函数统计",
            difficulty="简单",
            tags=["COUNT", "SUM", "AVG", "MAX", "MIN"],
            statement="给定支付表 `payments`，只统计 `status='success'` 的记录。输出 `payment_count,total_amount,avg_amount,max_amount,min_amount`，金额保留两位小数。",
            schema="payments(payment_id, status, amount)",
            solution_sql="""
select count(*) as payment_count,
       sum(amount)::numeric(10,2) as total_amount,
       round(avg(amount), 2)::numeric(10,2) as avg_amount,
       max(amount)::numeric(10,2) as max_amount,
       min(amount)::numeric(10,2) as min_amount
from payments
where status = 'success';
""",
            tests=mk_tests(
                """
create table payments(payment_id int primary key, status text, amount numeric(10,2));
insert into payments values
  (1, 'success', 10.00),
  (2, 'failed', 100.00),
  (3, 'success', 20.50),
  (4, 'success', 30.25);
""",
                """
create table payments(payment_id int primary key, status text, amount numeric(10,2));
insert into payments values
  (1, 'success', 1.00),
  (2, 'success', 2.00),
  (3, 'success', 2.00);
""",
                """
create table payments(payment_id int primary key, status text, amount numeric(10,2));
insert into payments values
  (1, 'failed', 99.00),
  (2, 'success', 100.00),
  (3, 'success', 50.00),
  (4, 'success', 25.00);
""",
            ),
        )
    )

    specs.append(
        ProblemSpec(
            slug="review-08-group-by-region-sales",
            title="GROUP BY 分组汇总",
            difficulty="简单",
            tags=["GROUP BY", "SUM"],
            statement="给定销售表 `sales`，按地区统计总销量和总销售额。输出 `region,total_qty,total_amount`，总金额保留两位小数，按地区升序。",
            schema="sales(sale_id, region, qty, amount)",
            solution_sql="""
select region,
       sum(qty) as total_qty,
       sum(amount)::numeric(10,2) as total_amount
from sales
group by region
order by region;
""",
            tests=mk_tests(
                """
create table sales(sale_id int primary key, region text, qty int, amount numeric(10,2));
insert into sales values
  (1, '华北', 2, 20.00),
  (2, '华北', 3, 45.50),
  (3, '华东', 1, 99.00);
""",
                """
create table sales(sale_id int primary key, region text, qty int, amount numeric(10,2));
insert into sales values
  (1, 'A', 1, 1.10),
  (2, 'B', 2, 2.20),
  (3, 'A', 3, 3.30),
  (4, 'C', 4, 4.40);
""",
                """
create table sales(sale_id int primary key, region text, qty int, amount numeric(10,2));
insert into sales values
  (10, '西南', 5, 100.00),
  (11, '西南', 5, 100.00),
  (12, '东北', 1, 8.50);
""",
            ),
        )
    )

    specs.append(
        ProblemSpec(
            slug="review-09-having-customers",
            title="HAVING 分组筛选",
            difficulty="中等",
            tags=["GROUP BY", "HAVING"],
            statement="给定订单表 `orders`，查询已支付订单数至少 2 且已支付总额不少于 100 的客户。输出 `customer_id,paid_count,paid_total`，按 `customer_id` 升序。",
            schema="orders(order_id, customer_id, status, amount)",
            solution_sql="""
select customer_id,
       count(*) as paid_count,
       sum(amount)::numeric(10,2) as paid_total
from orders
where status = 'paid'
group by customer_id
having count(*) >= 2 and sum(amount) >= 100
order by customer_id;
""",
            tests=mk_tests(
                """
create table orders(order_id int primary key, customer_id int, status text, amount numeric(10,2));
insert into orders values
  (1, 1, 'paid', 40.00),
  (2, 1, 'paid', 60.00),
  (3, 2, 'paid', 120.00),
  (4, 2, 'cancelled', 10.00),
  (5, 3, 'paid', 50.00),
  (6, 3, 'paid', 49.99);
""",
                """
create table orders(order_id int primary key, customer_id int, status text, amount numeric(10,2));
insert into orders values
  (1, 10, 'paid', 100.00),
  (2, 10, 'paid', 1.00),
  (3, 11, 'paid', 50.00),
  (4, 11, 'paid', 50.00);
""",
                """
create table orders(order_id int primary key, customer_id int, status text, amount numeric(10,2));
insert into orders values
  (1, 20, 'paid', 20.00),
  (2, 20, 'paid', 30.00),
  (3, 20, 'paid', 50.00),
  (4, 21, 'paid', 99.99),
  (5, 21, 'paid', 0.01);
""",
            ),
        )
    )

    specs.append(
        ProblemSpec(
            slug="review-10-join-employees-departments",
            title="JOIN 连接查询",
            difficulty="简单",
            tags=["JOIN"],
            statement="给定员工表 `employees` 和部门表 `departments`，查询在职员工的部门名称。输出 `emp_no,name,dept_name`，按员工编号升序。",
            schema="employees(emp_no, name, dept_no, active), departments(dept_no, dept_name)",
            solution_sql="""
select e.emp_no, e.name, d.dept_name
from employees e
join departments d on e.dept_no = d.dept_no
where e.active = true
order by e.emp_no;
""",
            tests=mk_tests(
                """
create table departments(dept_no text primary key, dept_name text);
create table employees(emp_no int primary key, name text, dept_no text references departments(dept_no), active boolean);
insert into departments values ('d001', '研发'), ('d002', '财务');
insert into employees values (1, 'Alice', 'd001', true), (2, 'Bob', 'd002', false), (3, 'Cindy', 'd002', true);
""",
                """
create table departments(dept_no text primary key, dept_name text);
create table employees(emp_no int primary key, name text, dept_no text references departments(dept_no), active boolean);
insert into departments values ('a', 'A部'), ('b', 'B部'), ('c', 'C部');
insert into employees values (10, 'T', 'a', true), (11, 'U', 'c', true), (12, 'V', 'b', false);
""",
                """
create table departments(dept_no text primary key, dept_name text);
create table employees(emp_no int primary key, name text, dept_no text references departments(dept_no), active boolean);
insert into departments values ('x', 'X'), ('y', 'Y');
insert into employees values (20, 'M', 'y', true), (21, 'N', 'x', true);
""",
            ),
        )
    )

    specs.append(
        ProblemSpec(
            slug="review-11-subquery-above-dept-average",
            title="子查询：高于部门平均工资",
            difficulty="中等",
            tags=["Subquery"],
            statement="给定员工表 `employees`，查询工资高于本部门平均工资的员工。输出 `emp_no,name,dept_no,salary`，按 `dept_no`、`salary` 降序、`emp_no` 升序排序。",
            schema="employees(emp_no, name, dept_no, salary)",
            solution_sql="""
select e.emp_no, e.name, e.dept_no, e.salary
from employees e
where e.salary > (
  select avg(e2.salary)
  from employees e2
  where e2.dept_no = e.dept_no
)
order by e.dept_no, e.salary desc, e.emp_no;
""",
            tests=mk_tests(
                """
create table employees(emp_no int primary key, name text, dept_no text, salary int);
insert into employees values
  (1, 'A', 'd1', 100),
  (2, 'B', 'd1', 200),
  (3, 'C', 'd2', 300),
  (4, 'D', 'd2', 100),
  (5, 'E', 'd2', 200);
""",
                """
create table employees(emp_no int primary key, name text, dept_no text, salary int);
insert into employees values
  (10, 'T', 'x', 10),
  (11, 'U', 'x', 10),
  (12, 'V', 'x', 20),
  (13, 'W', 'y', 99);
""",
                """
create table employees(emp_no int primary key, name text, dept_no text, salary int);
insert into employees values
  (20, 'M', 'a', 1),
  (21, 'N', 'a', 2),
  (22, 'O', 'a', 3),
  (23, 'P', 'b', 100),
  (24, 'Q', 'b', 50);
""",
            ),
        )
    )

    specs.append(
        ProblemSpec(
            slug="review-12-set-operations",
            title="UNION、INTERSECT、EXCEPT 集合查询",
            difficulty="中等",
            tags=["UNION", "INTERSECT", "EXCEPT"],
            statement="""给定春季和夏季活动报名表 `spring_members`、`summer_members`。

请输出三类集合结果：

- `all`：参加过任一活动的成员；
- `both`：两个活动都参加的成员；
- `spring_only`：只参加春季活动的成员。

输出 `member_id,relation`，按 `relation`、`member_id` 升序。""",
            schema="spring_members(member_id), summer_members(member_id)",
            solution_sql="""
select member_id, relation
from (
  select member_id, 'all' as relation
  from (select member_id from spring_members union select member_id from summer_members) u
  union all
  select member_id, 'both' as relation
  from (select member_id from spring_members intersect select member_id from summer_members) i
  union all
  select member_id, 'spring_only' as relation
  from (select member_id from spring_members except select member_id from summer_members) e
) x
order by relation, member_id;
""",
            tests=mk_tests(
                """
create table spring_members(member_id int);
create table summer_members(member_id int);
insert into spring_members values (1), (2), (3), (3);
insert into summer_members values (3), (4), (1);
""",
                """
create table spring_members(member_id int);
create table summer_members(member_id int);
insert into spring_members values (10), (11), (12);
insert into summer_members values (12), (13), (14);
""",
                """
create table spring_members(member_id int);
create table summer_members(member_id int);
insert into spring_members values (20), (21), (22), (23);
insert into summer_members values (20), (21);
""",
            ),
        )
    )

    specs.append(
        ProblemSpec(
            slug="review-13-dml-bonus-salary",
            title="DML：给获奖员工当前薪水加 10%",
            difficulty="中等",
            tags=["UPDATE", "DML", "program.md"],
            statement="将所有在 `emp_bonus` 中出现过的员工当前薪水增加 10%。当前薪水定义为 `salaries.to_date = '9999-01-01'`。结果四舍五入为整数。",
            schema="emp_bonus(emp_no, received, btype), salaries(emp_no, salary, from_date, to_date)",
            solution_sql="""
update salaries
set salary = round(salary * 1.10)::integer
where to_date = date '9999-01-01'
  and emp_no in (select distinct emp_no from emp_bonus);
""",
            judge_sql="""
select emp_no, salary
from salaries
where to_date = date '9999-01-01'
order by emp_no;
""",
            tests=mk_tests(
                """
create table emp_bonus(emp_no int not null, received timestamp not null, btype smallint not null);
create table salaries(emp_no int not null, salary int not null, from_date date not null, to_date date not null, primary key(emp_no, from_date));
insert into salaries values
  (10001, 60000, '2024-01-01', '9999-01-01'),
  (10002, 50000, '2024-01-01', '9999-01-01'),
  (10001, 55000, '2023-01-01', '2023-12-31');
insert into emp_bonus values (10001, '2026-01-01', 1);
""",
                """
create table emp_bonus(emp_no int not null, received timestamp not null, btype smallint not null);
create table salaries(emp_no int not null, salary int not null, from_date date not null, to_date date not null, primary key(emp_no, from_date));
insert into salaries values
  (1, 10001, '2026-01-01', '9999-01-01'),
  (2, 10005, '2026-01-01', '9999-01-01'),
  (3, 77777, '2026-01-01', '2026-06-30');
insert into emp_bonus values (1, '2026-02-01', 1), (1, '2026-03-01', 2), (3, '2026-04-01', 1);
""",
                """
create table emp_bonus(emp_no int not null, received timestamp not null, btype smallint not null);
create table salaries(emp_no int not null, salary int not null, from_date date not null, to_date date not null, primary key(emp_no, from_date));
insert into salaries values
  (10, 80000, '2026-01-01', '9999-01-01'),
  (11, 90000, '2026-01-01', '9999-01-01');
insert into emp_bonus values (11, '2026-05-01', 1);
""",
            ),
        )
    )

    specs.append(
        ProblemSpec(
            slug="review-14-ddl-spj-schema",
            title="DDL：创建 SPJ 表并定义主外键",
            difficulty="中等",
            tags=["CREATE", "PRIMARY KEY", "FOREIGN KEY", "SPJ", "PDF"],
            statement="""请创建小写表名 `s`、`p`、`j`、`spj`，对应供应商、零件、工程项目、供应情况。

要求：

- `s.sno`、`p.pno`、`j.jno` 为主键；
- `spj` 包含 `sno,pno,jno,qty`；
- `spj` 以 `(sno,pno,jno)` 为联合主键；
- `spj.sno/pno/jno` 分别引用 `s/p/j`。""",
            schema="本题无预置表，需要自行 CREATE TABLE。",
            solution_sql="""
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
  sno text not null references s(sno),
  pno text not null references p(pno),
  jno text not null references j(jno),
  qty integer not null,
  primary key (sno, pno, jno)
);
""",
            judge_sql="""
select check_name, ok
from (
  values
    ('j_pk', exists (select 1 from information_schema.table_constraints where table_name = 'j' and constraint_type = 'PRIMARY KEY')),
    ('p_pk', exists (select 1 from information_schema.table_constraints where table_name = 'p' and constraint_type = 'PRIMARY KEY')),
    ('s_pk', exists (select 1 from information_schema.table_constraints where table_name = 's' and constraint_type = 'PRIMARY KEY')),
    ('spj_fk_count', (select count(*) from information_schema.table_constraints where table_name = 'spj' and constraint_type = 'FOREIGN KEY') = 3),
    ('spj_pk', exists (select 1 from information_schema.table_constraints where table_name = 'spj' and constraint_type = 'PRIMARY KEY'))
) as checks(check_name, ok)
order by check_name;
""",
            tests=mk_tests("", "", ""),
        )
    )

    specs.append(
        ProblemSpec(
            slug="review-15-alter-drop-truncate",
            title="ALTER、DROP、TRUNCATE 综合操作",
            difficulty="中等",
            tags=["ALTER", "DROP", "TRUNCATE"],
            statement="""按顺序完成以下操作：

1. 给 `stage_orders` 增加 `status text not null default 'new'` 列；
2. 清空 `stage_orders` 原有数据；
3. 插入一行 `(99, 500)`，让 `status` 使用默认值；
4. 删除表 `obsolete_cache`。""",
            schema="stage_orders(order_id, amount), obsolete_cache(id)",
            solution_sql="""
alter table stage_orders add column status text not null default 'new';
truncate table stage_orders;
insert into stage_orders(order_id, amount) values (99, 500);
drop table obsolete_cache;
""",
            judge_sql="""
select item, value
from (
  select 'has_status' as item, exists (
    select 1 from information_schema.columns
    where table_name = 'stage_orders' and column_name = 'status' and is_nullable = 'NO'
  )::text as value
  union all
  select 'obsolete_exists', (to_regclass('obsolete_cache') is not null)::text
  union all
  select 'rows', string_agg(order_id::text || ':' || amount::text || ':' || status, ',' order by order_id)
  from stage_orders
) x
order by item;
""",
            tests=mk_tests(
                """
create table stage_orders(order_id int primary key, amount int not null);
insert into stage_orders values (1, 10), (2, 20);
create table obsolete_cache(id int);
insert into obsolete_cache values (1);
""",
                """
create table stage_orders(order_id int primary key, amount int not null);
insert into stage_orders values (10, 100);
create table obsolete_cache(id int);
""",
                """
create table stage_orders(order_id int primary key, amount int not null);
insert into stage_orders values (5, 50), (6, 60), (7, 70);
create table obsolete_cache(id int);
insert into obsolete_cache values (9);
""",
            ),
        )
    )

    specs.append(
        ProblemSpec(
            slug="review-16-constraints-default-check",
            title="完整性约束：非空、唯一、检查、默认值",
            difficulty="中等",
            tags=["Constraint", "DEFAULT", "CHECK", "UNIQUE"],
            statement="""创建表 `course_registrations`：

- `id integer primary key`
- `student_id integer not null`
- `course_code text not null`
- `credits integer not null default 3`
- `credits` 必须在 1 到 6 之间
- `(student_id, course_code)` 唯一""",
            schema="本题无预置表，需要自行 CREATE TABLE。",
            solution_sql="""
create table course_registrations (
  id integer primary key,
  student_id integer not null,
  course_code text not null,
  credits integer not null default 3 check (credits between 1 and 6),
  unique (student_id, course_code)
);
""",
            judge_sql="""
select item, ok
from (
  values
    ('table_exists', to_regclass('course_registrations') is not null),
    ('credits_default', exists (select 1 from information_schema.columns where table_name = 'course_registrations' and column_name = 'credits' and column_default like '%3%')),
    ('not_null_count', (select count(*) from information_schema.columns where table_name = 'course_registrations' and is_nullable = 'NO') >= 4),
    ('unique_exists', exists (select 1 from information_schema.table_constraints where table_name = 'course_registrations' and constraint_type = 'UNIQUE')),
    ('check_exists', exists (select 1 from information_schema.table_constraints where table_name = 'course_registrations' and constraint_type = 'CHECK'))
) as checks(item, ok)
order by item;
""",
            tests=mk_tests("", "", ""),
        )
    )

    specs.append(
        ProblemSpec(
            slug="review-17-view-actor-name",
            title="视图：actor_name_view",
            difficulty="简单",
            tags=["VIEW", "program.md"],
            statement="针对 `actor` 表创建视图 `actor_name_view`，只包含 `first_name` 和 `last_name` 两列，并分别重命名为 `first_name_v`、`last_name_v`。",
            schema="actor(actor_id, first_name, last_name, last_update)",
            solution_sql="""
create view actor_name_view as
select first_name as first_name_v,
       last_name as last_name_v
from actor;
""",
            judge_sql="""
select item, value
from (
  select 'columns' as item, string_agg(column_name, ',' order by ordinal_position) as value
  from information_schema.columns
  where table_name = 'actor_name_view'
  union all
  select 'rows', string_agg(first_name_v || ':' || last_name_v, ',' order by first_name_v, last_name_v)
  from actor_name_view
) x
order by item;
""",
            tests=mk_tests(
                """
create table actor(actor_id smallint primary key, first_name text not null, last_name text not null, last_update timestamp not null default current_timestamp);
insert into actor(actor_id, first_name, last_name) values (1, 'PENELOPE', 'GUINESS'), (2, 'NICK', 'WAHLBERG');
""",
                """
create table actor(actor_id smallint primary key, first_name text not null, last_name text not null, last_update timestamp not null default current_timestamp);
insert into actor(actor_id, first_name, last_name) values (10, 'ED', 'CHASE');
""",
                """
create table actor(actor_id smallint primary key, first_name text not null, last_name text not null, last_update timestamp not null default current_timestamp);
insert into actor(actor_id, first_name, last_name) values (20, 'A', 'Z'), (21, 'A', 'Y'), (22, 'B', 'X');
""",
            ),
        )
    )

    specs.append(
        ProblemSpec(
            slug="review-18-index-orders",
            title="索引 INDEX",
            difficulty="简单",
            tags=["INDEX"],
            statement="在 `orders(customer_id, order_date)` 上创建名为 `idx_orders_customer_date` 的普通索引。",
            schema="orders(order_id, customer_id, order_date, amount)",
            solution_sql="create index idx_orders_customer_date on orders(customer_id, order_date);",
            judge_sql="""
select indexname, has_customer_date
from (
  select indexname,
         (indexdef like '%(customer_id, order_date)%') as has_customer_date
  from pg_indexes
  where schemaname = 'public' and tablename = 'orders' and indexname = 'idx_orders_customer_date'
) x
order by indexname;
""",
            tests=mk_tests(
                "create table orders(order_id int primary key, customer_id int, order_date date, amount numeric(10,2));",
                "create table orders(order_id int primary key, customer_id int, order_date date, amount numeric(10,2));",
                "create table orders(order_id int primary key, customer_id int, order_date date, amount numeric(10,2));",
            ),
        )
    )

    specs.append(
        ProblemSpec(
            slug="review-19-grant-revoke-public",
            title="GRANT、REVOKE 对象权限",
            difficulty="中等",
            tags=["GRANT", "REVOKE", "DCL", "PDF"],
            statement="""`reports` 表已存在，且当前 `PUBLIC` 已拥有 `INSERT` 权限。

请完成权限控制：

1. 授予 `PUBLIC` 对 `reports` 的 `SELECT` 权限；
2. 撤销 `PUBLIC` 对 `reports` 的 `INSERT` 权限。""",
            schema="reports(report_id, title)",
            solution_sql="""
grant select on reports to public;
revoke insert on reports from public;
""",
            judge_sql="""
select privilege_type
from information_schema.table_privileges
where table_schema = 'public'
  and table_name = 'reports'
  and grantee = 'PUBLIC'
order by privilege_type;
""",
            tests=mk_tests(
                """
create table reports(report_id int primary key, title text);
grant insert on reports to public;
""",
                """
create table reports(report_id int primary key, title text);
grant insert on reports to public;
""",
                """
create table reports(report_id int primary key, title text);
grant insert on reports to public;
""",
            ),
        )
    )

    specs.append(
        ProblemSpec(
            slug="review-20-transaction-rollback",
            title="COMMIT、ROLLBACK 事务控制",
            difficulty="中等",
            tags=["COMMIT", "ROLLBACK", "TCL"],
            statement="""完成一次转账练习：

1. 先开启事务，尝试从 1 号账户扣 100 并写入 `dry-run` 审计，然后回滚；
2. 再开启事务，从 1 号账户扣 50、给 2 号账户加 50，并写入 `transfer-50` 审计，然后提交。""",
            schema="accounts(account_id, balance), audit_logs(message)",
            solution_sql="""
begin;
update accounts set balance = balance - 100 where account_id = 1;
insert into audit_logs(message) values ('dry-run');
rollback;

begin;
update accounts set balance = balance - 50 where account_id = 1;
update accounts set balance = balance + 50 where account_id = 2;
insert into audit_logs(message) values ('transfer-50');
commit;
""",
            judge_sql="""
select section, item, value
from (
  select 'account' as section, account_id::text as item, balance::text as value from accounts
  union all
  select 'audit', log_id::text, message from audit_logs
) x
order by section, item;
""",
            tests=mk_tests(
                """
create table accounts(account_id int primary key, balance int not null);
create table audit_logs(log_id serial primary key, message text not null);
insert into accounts values (1, 1000), (2, 200);
""",
                """
create table accounts(account_id int primary key, balance int not null);
create table audit_logs(log_id serial primary key, message text not null);
insert into accounts values (1, 50), (2, 50);
""",
                """
create table accounts(account_id int primary key, balance int not null);
create table audit_logs(log_id serial primary key, message text not null);
insert into accounts values (1, 500), (2, 700), (3, 900);
""",
            ),
        )
    )

    specs.append(
        ProblemSpec(
            slug="review-21-sql-command-categories",
            title="DDL、DML、DQL、DCL、TCL 分类",
            difficulty="简单",
            tags=["DDL", "DML", "DQL", "DCL", "TCL"],
            statement="给定 SQL 语句首关键字表 `sql_keywords`，按关键字分类。输出 `id,category`，分类只能是 `DDL`、`DML`、`DQL`、`DCL`、`TCL`。",
            schema="sql_keywords(id, keyword)",
            solution_sql="""
select id,
       case
         when upper(keyword) in ('CREATE', 'ALTER', 'DROP', 'TRUNCATE') then 'DDL'
         when upper(keyword) in ('INSERT', 'UPDATE', 'DELETE') then 'DML'
         when upper(keyword) = 'SELECT' then 'DQL'
         when upper(keyword) in ('GRANT', 'REVOKE') then 'DCL'
         when upper(keyword) in ('COMMIT', 'ROLLBACK') then 'TCL'
       end as category
from sql_keywords
order by id;
""",
            tests=mk_tests(
                """
create table sql_keywords(id int primary key, keyword text);
insert into sql_keywords values (1, 'SELECT'), (2, 'CREATE'), (3, 'GRANT'), (4, 'COMMIT'), (5, 'INSERT');
""",
                """
create table sql_keywords(id int primary key, keyword text);
insert into sql_keywords values (10, 'ALTER'), (11, 'DROP'), (12, 'REVOKE'), (13, 'ROLLBACK'), (14, 'DELETE');
""",
                """
create table sql_keywords(id int primary key, keyword text);
insert into sql_keywords values (20, 'TRUNCATE'), (21, 'UPDATE'), (22, 'SELECT'), (23, 'CREATE');
""",
            ),
        )
    )

    specs.append(
        ProblemSpec(
            slug="review-23-name-suffix-order",
            title="按照 first_name 最后两个字母排序",
            difficulty="简单",
            tags=["ORDER BY", "string function", "program.md"],
            statement="获取 `employees` 中的 `first_name`，按照 `first_name` 最后两个字母升序排序；若最后两个字母相同，再按 `first_name` 升序。",
            schema="employees(emp_no, birth_date, first_name, last_name, gender, hire_date)",
            solution_sql="""
select first_name
from employees
order by right(first_name, 2), first_name;
""",
            tests=mk_tests(
                """
create table employees(emp_no int primary key, birth_date date, first_name text, last_name text, gender char(1), hire_date date);
insert into employees values
  (1, '1990-01-01', 'Georgi', 'A', 'M', '2020-01-01'),
  (2, '1990-01-01', 'Mary', 'B', 'F', '2020-01-01'),
  (3, '1990-01-01', 'Anneke', 'C', 'F', '2020-01-01'),
  (4, '1990-01-01', 'Parto', 'D', 'M', '2020-01-01');
""",
                """
create table employees(emp_no int primary key, birth_date date, first_name text, last_name text, gender char(1), hire_date date);
insert into employees values
  (10, '1990-01-01', 'Alex', 'A', 'M', '2020-01-01'),
  (11, '1990-01-01', 'Bex', 'B', 'F', '2020-01-01'),
  (12, '1990-01-01', 'Cara', 'C', 'F', '2020-01-01');
""",
                """
create table employees(emp_no int primary key, birth_date date, first_name text, last_name text, gender char(1), hire_date date);
insert into employees values
  (20, '1990-01-01', 'Kyoichi', 'A', 'M', '2020-01-01'),
  (21, '1990-01-01', 'Tzvetan', 'B', 'M', '2020-01-01'),
  (22, '1990-01-01', 'Sumant', 'C', 'M', '2020-01-01');
""",
            ),
        )
    )

    specs.append(
        ProblemSpec(
            slug="review-24-latest-hire-employee",
            title="查找最晚入职员工",
            difficulty="简单",
            tags=["Subquery", "program.md"],
            statement="查找最晚入职员工的所有信息。若多人同一天入职，全部输出。输出 `emp_no,birth_date,first_name,last_name,gender,hire_date`，按 `emp_no` 升序。",
            schema="employees(emp_no, birth_date, first_name, last_name, gender, hire_date)",
            solution_sql="""
select emp_no, birth_date, first_name, last_name, gender, hire_date
from employees
where hire_date = (select max(hire_date) from employees)
order by emp_no;
""",
            tests=mk_tests(
                """
create table employees(emp_no int primary key, birth_date date, first_name text, last_name text, gender char(1), hire_date date);
insert into employees values
  (10001, '1953-09-02', 'Georgi', 'Facello', 'M', '1986-06-26'),
  (10008, '1958-02-19', 'Saniya', 'Kalloufi', 'M', '1994-09-15'),
  (10009, '1952-04-19', 'Sumant', 'Peac', 'F', '1985-02-18');
""",
                """
create table employees(emp_no int primary key, birth_date date, first_name text, last_name text, gender char(1), hire_date date);
insert into employees values
  (1, '1990-01-01', 'A', 'A', 'M', '2020-01-01'),
  (2, '1990-01-01', 'B', 'B', 'F', '2021-01-01'),
  (3, '1990-01-01', 'C', 'C', 'F', '2021-01-01');
""",
                """
create table employees(emp_no int primary key, birth_date date, first_name text, last_name text, gender char(1), hire_date date);
insert into employees values
  (10, '1990-01-01', 'T', 'T', 'M', '2000-01-01'),
  (11, '1990-01-01', 'U', 'U', 'M', '1999-01-01');
""",
            ),
        )
    )

    specs.append(
        ProblemSpec(
            slug="review-25-starting-salary",
            title="查找所有员工入职时薪水",
            difficulty="中等",
            tags=["JOIN", "program.md"],
            statement="查找所有员工入职时候的薪水，输出 `emp_no,salary`，按 `emp_no` 降序。入职时候薪水指 `employees.hire_date = salaries.from_date`。",
            schema="employees(emp_no, hire_date), salaries(emp_no, salary, from_date, to_date)",
            solution_sql="""
select e.emp_no, s.salary
from employees e
join salaries s on s.emp_no = e.emp_no and s.from_date = e.hire_date
order by e.emp_no desc;
""",
            tests=mk_tests(
                """
create table employees(emp_no int primary key, birth_date date, first_name text, last_name text, gender char(1), hire_date date);
create table salaries(emp_no int, salary int, from_date date, to_date date, primary key(emp_no, from_date));
insert into employees values
  (10001, '1953-09-02', 'Georgi', 'Facello', 'M', '1986-06-26'),
  (10002, '1964-06-02', 'Bezalel', 'Simmel', 'F', '1985-11-21');
insert into salaries values
  (10001, 60117, '1986-06-26', '1987-06-26'),
  (10001, 62102, '1987-06-26', '9999-01-01'),
  (10002, 65828, '1985-11-21', '9999-01-01');
""",
                """
create table employees(emp_no int primary key, birth_date date, first_name text, last_name text, gender char(1), hire_date date);
create table salaries(emp_no int, salary int, from_date date, to_date date, primary key(emp_no, from_date));
insert into employees values
  (1, '1990-01-01', 'A', 'A', 'M', '2020-01-01'),
  (2, '1990-01-01', 'B', 'B', 'F', '2021-01-01'),
  (3, '1990-01-01', 'C', 'C', 'F', '2022-01-01');
insert into salaries values
  (1, 10, '2020-01-01', '9999-01-01'),
  (2, 20, '2021-01-01', '9999-01-01'),
  (3, 30, '2021-01-01', '2022-01-01'),
  (3, 40, '2022-01-01', '9999-01-01');
""",
                """
create table employees(emp_no int primary key, birth_date date, first_name text, last_name text, gender char(1), hire_date date);
create table salaries(emp_no int, salary int, from_date date, to_date date, primary key(emp_no, from_date));
insert into employees values (9, '1990-01-01', 'Z', 'Z', 'M', '2010-01-01');
insert into salaries values (9, 999, '2010-01-01', '9999-01-01');
""",
            ),
        )
    )

    specs.append(
        ProblemSpec(
            slug="review-26-current-manager-salary",
            title="获取当前部门经理当前薪水",
            difficulty="中等",
            tags=["JOIN", "program.md"],
            statement="获取所有部门当前 manager 的当前薪水。当前定义为 `to_date='9999-01-01'`。输出 `dept_no,emp_no,salary`，按 `dept_no` 升序。",
            schema="dept_manager(dept_no, emp_no, from_date, to_date), salaries(emp_no, salary, from_date, to_date)",
            solution_sql="""
select dm.dept_no, dm.emp_no, s.salary
from dept_manager dm
join salaries s on s.emp_no = dm.emp_no
where dm.to_date = date '9999-01-01'
  and s.to_date = date '9999-01-01'
order by dm.dept_no;
""",
            tests=mk_tests(
                """
create table dept_manager(dept_no char(4), emp_no int, from_date date, to_date date, primary key(emp_no, dept_no));
create table salaries(emp_no int, salary int, from_date date, to_date date, primary key(emp_no, from_date));
insert into dept_manager values ('d001', 10002, '2020-01-01', '9999-01-01'), ('d002', 10006, '2020-01-01', '9999-01-01'), ('d003', 10007, '2020-01-01', '2021-01-01');
insert into salaries values (10002, 72527, '2020-01-01', '9999-01-01'), (10006, 43311, '2020-01-01', '9999-01-01'), (10007, 1, '2020-01-01', '9999-01-01');
""",
                """
create table dept_manager(dept_no char(4), emp_no int, from_date date, to_date date, primary key(emp_no, dept_no));
create table salaries(emp_no int, salary int, from_date date, to_date date, primary key(emp_no, from_date));
insert into dept_manager values ('a001', 1, '2020-01-01', '9999-01-01'), ('a002', 2, '2020-01-01', '9999-01-01');
insert into salaries values (1, 10, '2020-01-01', '2021-01-01'), (1, 11, '2021-01-01', '9999-01-01'), (2, 20, '2020-01-01', '9999-01-01');
""",
                """
create table dept_manager(dept_no char(4), emp_no int, from_date date, to_date date, primary key(emp_no, dept_no));
create table salaries(emp_no int, salary int, from_date date, to_date date, primary key(emp_no, from_date));
insert into dept_manager values ('x001', 9, '2020-01-01', '2021-01-01'), ('x001', 10, '2021-01-01', '9999-01-01');
insert into salaries values (9, 90, '2020-01-01', '9999-01-01'), (10, 100, '2021-01-01', '9999-01-01');
""",
            ),
        )
    )

    specs.append(
        ProblemSpec(
            slug="review-27-current-employee-manager",
            title="获取所有员工当前 manager",
            difficulty="中等",
            tags=["JOIN", "program.md"],
            statement="获取所有员工当前 manager。如果当前 manager 是自己则不显示。输出 `emp_no,manager_no`，按 `emp_no` 升序。",
            schema="dept_emp(emp_no, dept_no, from_date, to_date), dept_manager(dept_no, emp_no, from_date, to_date)",
            solution_sql="""
select de.emp_no, dm.emp_no as manager_no
from dept_emp de
join dept_manager dm on dm.dept_no = de.dept_no
where de.to_date = date '9999-01-01'
  and dm.to_date = date '9999-01-01'
  and de.emp_no <> dm.emp_no
order by de.emp_no;
""",
            tests=mk_tests(
                """
create table dept_emp(emp_no int, dept_no char(4), from_date date, to_date date, primary key(emp_no, dept_no));
create table dept_manager(dept_no char(4), emp_no int, from_date date, to_date date, primary key(emp_no, dept_no));
insert into dept_emp values (10001, 'd001', '2020-01-01', '9999-01-01'), (10002, 'd001', '2020-01-01', '9999-01-01'), (10003, 'd002', '2020-01-01', '9999-01-01');
insert into dept_manager values ('d001', 10002, '2020-01-01', '9999-01-01'), ('d002', 10004, '2020-01-01', '9999-01-01');
""",
                """
create table dept_emp(emp_no int, dept_no char(4), from_date date, to_date date, primary key(emp_no, dept_no));
create table dept_manager(dept_no char(4), emp_no int, from_date date, to_date date, primary key(emp_no, dept_no));
insert into dept_emp values (1, 'a001', '2020-01-01', '9999-01-01'), (2, 'a001', '2020-01-01', '2021-01-01'), (3, 'a002', '2020-01-01', '9999-01-01');
insert into dept_manager values ('a001', 1, '2020-01-01', '9999-01-01'), ('a002', 4, '2020-01-01', '9999-01-01');
""",
                """
create table dept_emp(emp_no int, dept_no char(4), from_date date, to_date date, primary key(emp_no, dept_no));
create table dept_manager(dept_no char(4), emp_no int, from_date date, to_date date, primary key(emp_no, dept_no));
insert into dept_emp values (10, 'x001', '2020-01-01', '9999-01-01'), (11, 'x001', '2020-01-01', '9999-01-01');
insert into dept_manager values ('x001', 12, '2020-01-01', '9999-01-01');
""",
            ),
        )
    )

    specs.append(
        ProblemSpec(
            slug="review-28-dept-raise-count",
            title="统计各部门员工涨薪次数总和",
            difficulty="中等",
            tags=["Window", "GROUP BY", "program.md"],
            statement="给定部门、部门员工、薪水历史，统计每个部门员工的涨薪次数总和。每名员工薪水历史中第一条不算涨薪，其后每条算 1 次。输出 `dept_no,dept_name,raise_count`，按 `dept_no` 升序。",
            schema="departments(dept_no, dept_name), dept_emp(emp_no, dept_no), salaries(emp_no, salary, from_date, to_date)",
            solution_sql="""
with salary_events as (
  select emp_no,
         row_number() over (partition by emp_no order by from_date) as rn
  from salaries
)
select d.dept_no,
       d.dept_name,
       count(*) filter (where se.rn > 1) as raise_count
from departments d
join dept_emp de on de.dept_no = d.dept_no
join salary_events se on se.emp_no = de.emp_no
group by d.dept_no, d.dept_name
order by d.dept_no;
""",
            tests=mk_tests(
                """
create table departments(dept_no char(4) primary key, dept_name text);
create table dept_emp(emp_no int, dept_no char(4), from_date date, to_date date, primary key(emp_no, dept_no));
create table salaries(emp_no int, salary int, from_date date, to_date date, primary key(emp_no, from_date));
insert into departments values ('d001', 'Marketing'), ('d002', 'Finance');
insert into dept_emp values (1, 'd001', '2020-01-01', '9999-01-01'), (2, 'd001', '2020-01-01', '9999-01-01'), (3, 'd002', '2020-01-01', '9999-01-01');
insert into salaries values
  (1, 10, '2020-01-01', '2021-01-01'), (1, 20, '2021-01-01', '9999-01-01'),
  (2, 10, '2020-01-01', '2021-01-01'), (2, 20, '2021-01-01', '2022-01-01'), (2, 30, '2022-01-01', '9999-01-01'),
  (3, 50, '2020-01-01', '9999-01-01');
""",
                """
create table departments(dept_no char(4) primary key, dept_name text);
create table dept_emp(emp_no int, dept_no char(4), from_date date, to_date date, primary key(emp_no, dept_no));
create table salaries(emp_no int, salary int, from_date date, to_date date, primary key(emp_no, from_date));
insert into departments values ('a001', 'A'), ('a002', 'B');
insert into dept_emp values (10, 'a001', '2020-01-01', '9999-01-01'), (11, 'a002', '2020-01-01', '9999-01-01');
insert into salaries values
  (10, 1, '2020-01-01', '2021-01-01'), (10, 2, '2021-01-01', '2022-01-01'), (10, 3, '2022-01-01', '9999-01-01'),
  (11, 9, '2020-01-01', '9999-01-01');
""",
                """
create table departments(dept_no char(4) primary key, dept_name text);
create table dept_emp(emp_no int, dept_no char(4), from_date date, to_date date, primary key(emp_no, dept_no));
create table salaries(emp_no int, salary int, from_date date, to_date date, primary key(emp_no, from_date));
insert into departments values ('x001', 'X');
insert into dept_emp values (20, 'x001', '2020-01-01', '9999-01-01'), (21, 'x001', '2020-01-01', '9999-01-01');
insert into salaries values
  (20, 1, '2020-01-01', '9999-01-01'),
  (21, 1, '2020-01-01', '2021-01-01'), (21, 2, '2021-01-01', '9999-01-01');
""",
            ),
        )
    )

    specs.append(
        ProblemSpec(
            slug="review-29-current-title-avg-salary",
            title="统计当前 title 的当前平均薪水",
            difficulty="中等",
            tags=["AVG", "JOIN", "program.md"],
            statement="统计当前各个 title 类型对应员工当前薪水的平均工资。当前定义为 `to_date='9999-01-01'`。输出 `title,avg_salary`，平均工资保留 1 位小数，按 title 升序。",
            schema="titles(emp_no, title, from_date, to_date), salaries(emp_no, salary, from_date, to_date)",
            solution_sql="""
select t.title,
       round(avg(s.salary), 1)::numeric(10,1) as avg_salary
from titles t
join salaries s on s.emp_no = t.emp_no
where t.to_date = date '9999-01-01'
  and s.to_date = date '9999-01-01'
group by t.title
order by t.title;
""",
            tests=mk_tests(
                """
create table salaries(emp_no int, salary int, from_date date, to_date date, primary key(emp_no, from_date));
create table titles(emp_no int, title text, from_date date, to_date date);
insert into salaries values (1, 100, '2020-01-01', '9999-01-01'), (2, 200, '2020-01-01', '9999-01-01'), (3, 500, '2020-01-01', '2021-01-01'), (3, 600, '2021-01-01', '9999-01-01');
insert into titles values (1, 'Engineer', '2020-01-01', '9999-01-01'), (2, 'Engineer', '2020-01-01', '9999-01-01'), (3, 'Staff', '2020-01-01', '2021-01-01');
""",
                """
create table salaries(emp_no int, salary int, from_date date, to_date date, primary key(emp_no, from_date));
create table titles(emp_no int, title text, from_date date, to_date date);
insert into salaries values (10, 10, '2020-01-01', '9999-01-01'), (11, 20, '2020-01-01', '9999-01-01'), (12, 30, '2020-01-01', '9999-01-01');
insert into titles values (10, 'A', '2020-01-01', '9999-01-01'), (11, 'B', '2020-01-01', '9999-01-01'), (12, 'B', '2020-01-01', '9999-01-01');
""",
                """
create table salaries(emp_no int, salary int, from_date date, to_date date, primary key(emp_no, from_date));
create table titles(emp_no int, title text, from_date date, to_date date);
insert into salaries values (20, 94409, '2020-01-01', '9999-01-01'), (21, 72527, '2020-01-01', '9999-01-01');
insert into titles values (20, 'Engineer', '2020-01-01', '9999-01-01'), (21, 'Staff', '2020-01-01', '9999-01-01');
""",
            ),
        )
    )

    specs.append(
        ProblemSpec(
            slug="review-30-spj-red-suppliers-j1",
            title="SPJ：供应 J1 红色零件的供应商",
            difficulty="中等",
            tags=["SPJ", "JOIN", "PDF"],
            statement="基于 SPJ 数据库，求供应工程 `J1` 且零件颜色为红色的供应商号码。输出 `sno`，去重并升序。",
            schema="s(sno,sname,status,city), p(pno,pname,color,weight), j(jno,jname,city), spj(sno,pno,jno,qty)",
            solution_sql="""
select distinct spj.sno
from spj
join p on p.pno = spj.pno
where spj.jno = 'J1' and p.color = '红'
order by spj.sno;
""",
            tests=mk_tests(
                spj_setup(),
                spj_setup(
                    "insert into spj values ('S4','P1','J1',50) on conflict do nothing;"
                ),
                spj_setup("update p set color = '绿' where pno = 'P1';"),
            ),
        )
    )

    specs.append(
        ProblemSpec(
            slug="review-31-spj-no-tianjin-red-projects",
            title="SPJ：没有使用天津供应商红色零件的工程",
            difficulty="困难",
            tags=["SPJ", "Subquery", "NOT EXISTS", "PDF"],
            statement="基于 SPJ 数据库，求没有使用天津供应商生产的红色零件的工程号。建议使用 `NOT EXISTS`。输出 `jno`，按工程号升序。",
            schema="s(sno,sname,status,city), p(pno,pname,color,weight), j(jno,jname,city), spj(sno,pno,jno,qty)",
            solution_sql="""
select j.jno
from j
where not exists (
  select 1
  from spj
  join s on s.sno = spj.sno
  join p on p.pno = spj.pno
  where spj.jno = j.jno
    and s.city = '天津'
    and p.color = '红'
)
order by j.jno;
""",
            tests=mk_tests(
                spj_setup(),
                spj_setup(
                    "delete from spj where sno = 'S1' and pno = 'P1' and jno = 'J3';"
                ),
                spj_setup("update s set city = '北京' where sno = 'S4';"),
            ),
        )
    )

    specs.append(
        ProblemSpec(
            slug="review-32-spj-division-s1-parts",
            title="SPJ：至少使用 S1 供应全部零件的工程",
            difficulty="困难",
            tags=["SPJ", "Division", "NOT EXISTS", "HAVING", "PDF"],
            statement="基于 SPJ 数据库，求至少使用了供应商 `S1` 所供应的全部零件的工程号。输出 `jno`，按工程号升序。",
            schema="s(sno,sname,status,city), p(pno,pname,color,weight), j(jno,jname,city), spj(sno,pno,jno,qty)",
            solution_sql="""
select candidate.jno
from (
  select distinct jno from spj
) candidate
where not exists (
  select 1
  from (select distinct pno from spj where sno = 'S1') s1_parts
  where not exists (
    select 1
    from spj used_parts
    where used_parts.jno = candidate.jno
      and used_parts.pno = s1_parts.pno
  )
)
order by candidate.jno;
""",
            tests=mk_tests(
                spj_setup(),
                spj_setup(
                    "insert into spj values ('S3','P2','J1',10) on conflict do nothing;"
                ),
                spj_setup(
                    "delete from spj where sno = 'S5' and pno = 'P2' and jno = 'J4';"
                ),
            ),
        )
    )

    specs.append(
        ProblemSpec(
            slug="review-33-spj-sanjian-view",
            title="SPJ：为三建工程创建供应视图",
            difficulty="中等",
            tags=["SPJ", "VIEW", "PDF"],
            statement="""为三建工程项目建立视图 `v_sanjian_spj`，包含 `sno,pno,qty` 三列。

三建工程指 `j.jname = '三建'`。""",
            schema="s(sno,sname,status,city), p(pno,pname,color,weight), j(jno,jname,city), spj(sno,pno,jno,qty)",
            solution_sql="""
create view v_sanjian_spj as
select spj.sno, spj.pno, spj.qty
from spj
join j on j.jno = spj.jno
where j.jname = '三建';
""",
            judge_sql="""
select section, key, value
from (
  select 'part_total' as section, pno as key, sum(qty)::text as value
  from v_sanjian_spj
  group by pno
  union all
  select 's1_supply', pno, qty::text
  from v_sanjian_spj
  where sno = 'S1'
) x
order by section, key, value;
""",
            tests=mk_tests(
                spj_setup(),
                spj_setup(
                    "insert into spj values ('S1','P2','J1',25) on conflict do nothing;"
                ),
                spj_setup("update j set jname = '三建' where jno = 'J2';"),
            ),
        )
    )

    specs.append(
        ProblemSpec(
            slug="review-34-acid-classification",
            title="事务 ACID 特性判断",
            difficulty="简单",
            tags=["ACID", "Theory"],
            statement="给定事务现象表 `acid_cases`，根据 `clue` 输出 ACID 特性名称。输出 `case_id,property_name`，名称使用 `Atomicity`、`Consistency`、`Isolation`、`Durability`。",
            schema="acid_cases(case_id, clue)",
            solution_sql="""
select case_id,
       case clue
         when 'all_or_nothing' then 'Atomicity'
         when 'valid_state' then 'Consistency'
         when 'concurrent_hidden' then 'Isolation'
         when 'committed_survives' then 'Durability'
       end as property_name
from acid_cases
order by case_id;
""",
            tests=mk_tests(
                """
create table acid_cases(case_id int primary key, clue text);
insert into acid_cases values (1, 'all_or_nothing'), (2, 'committed_survives'), (3, 'concurrent_hidden'), (4, 'valid_state');
""",
                """
create table acid_cases(case_id int primary key, clue text);
insert into acid_cases values (10, 'valid_state'), (11, 'all_or_nothing');
""",
                """
create table acid_cases(case_id int primary key, clue text);
insert into acid_cases values (20, 'concurrent_hidden'), (21, 'committed_survives'), (22, 'all_or_nothing');
""",
            ),
        )
    )

    specs.append(
        ProblemSpec(
            slug="review-35-normal-form-classification",
            title="函数依赖与范式判断",
            difficulty="中等",
            tags=["FD", "Normal Form", "Theory"],
            statement="""给定关系模式案例表 `nf_cases`：

- `all_attrs_atomic=false` 表示未达到 1NF，输出 `UNF`；
- 存在部分函数依赖时，最高只到 `1NF`；
- 无部分依赖但存在传递依赖时，最高到 `2NF`；
- 三者都满足时输出 `3NF`。

输出 `case_id,highest_nf`。""",
            schema="nf_cases(case_id, all_attrs_atomic, has_partial_dependency, has_transitive_dependency)",
            solution_sql="""
select case_id,
       case
         when not all_attrs_atomic then 'UNF'
         when has_partial_dependency then '1NF'
         when has_transitive_dependency then '2NF'
         else '3NF'
       end as highest_nf
from nf_cases
order by case_id;
""",
            tests=mk_tests(
                """
create table nf_cases(case_id int primary key, all_attrs_atomic boolean, has_partial_dependency boolean, has_transitive_dependency boolean);
insert into nf_cases values
  (1, false, false, false),
  (2, true, true, false),
  (3, true, false, true),
  (4, true, false, false);
""",
                """
create table nf_cases(case_id int primary key, all_attrs_atomic boolean, has_partial_dependency boolean, has_transitive_dependency boolean);
insert into nf_cases values
  (10, true, false, false),
  (11, true, true, true);
""",
                """
create table nf_cases(case_id int primary key, all_attrs_atomic boolean, has_partial_dependency boolean, has_transitive_dependency boolean);
insert into nf_cases values
  (20, false, true, true),
  (21, true, false, true),
  (22, true, false, false);
""",
            ),
        )
    )

    specs.append(
        ProblemSpec(
            slug="review-36-er-to-relations",
            title="E-R 图向关系模式转换",
            difficulty="中等",
            tags=["E-R", "Theory"],
            statement="""给定 E-R 转换片段 `er_items`，按本题规则输出需要新增的关系模式数量 `extra_relations`：

- `strong_entity`、`weak_entity`：自身形成 1 个关系；
- `one_to_many`：外键并入多方，新增 0 个关系；
- `many_to_many`：联系本身新增 1 个关系；
- `multivalued_attribute`：新增 1 个关系。

输出 `item_id,extra_relations`。""",
            schema="er_items(item_id, item_type)",
            solution_sql="""
select item_id,
       case item_type
         when 'strong_entity' then 1
         when 'weak_entity' then 1
         when 'one_to_many' then 0
         when 'many_to_many' then 1
         when 'multivalued_attribute' then 1
       end as extra_relations
from er_items
order by item_id;
""",
            tests=mk_tests(
                """
create table er_items(item_id int primary key, item_type text);
insert into er_items values (1, 'strong_entity'), (2, 'one_to_many'), (3, 'many_to_many'), (4, 'multivalued_attribute');
""",
                """
create table er_items(item_id int primary key, item_type text);
insert into er_items values (10, 'weak_entity'), (11, 'one_to_many');
""",
                """
create table er_items(item_id int primary key, item_type text);
insert into er_items values (20, 'many_to_many'), (21, 'strong_entity'), (22, 'multivalued_attribute');
""",
            ),
        )
    )

    specs.append(
        ProblemSpec(
            slug="review-37-three-schema-levels",
            title="外模式、模式、内模式分类",
            difficulty="简单",
            tags=["Three Schema", "Theory"],
            statement="给定三级模式案例表 `schema_cases`，根据 `level_code` 输出 `外模式`、`模式`、`内模式`。输出 `case_id,level_name`。",
            schema="schema_cases(case_id, level_code)",
            solution_sql="""
select case_id,
       case level_code
         when 'external' then '外模式'
         when 'conceptual' then '模式'
         when 'internal' then '内模式'
       end as level_name
from schema_cases
order by case_id;
""",
            tests=mk_tests(
                """
create table schema_cases(case_id int primary key, level_code text);
insert into schema_cases values (1, 'external'), (2, 'conceptual'), (3, 'internal');
""",
                """
create table schema_cases(case_id int primary key, level_code text);
insert into schema_cases values (10, 'internal'), (11, 'external');
""",
                """
create table schema_cases(case_id int primary key, level_code text);
insert into schema_cases values (20, 'conceptual'), (21, 'external'), (22, 'internal');
""",
            ),
        )
    )

    specs.append(
        ProblemSpec(
            slug="review-38-lab-zhang-teacher-experiments",
            title="A卷：查询张sir带过的实验名称",
            difficulty="简单",
            tags=["JOIN", "Exam"],
            statement="""实验管理数据库包含实验师、学生、实验、实验记录四张表。

请查询实验师 `张sir` 带过的所有实验。

输出 `experiment_id,experiment_name`，去重后按 `experiment_id` 升序。""",
            schema="lab_teachers, students, experiments, experiment_records",
            solution_sql="""
select distinct e.experiment_id, e.experiment_name
from lab_teachers t
join experiment_records r on r.teacher_id = t.teacher_id
join experiments e on e.experiment_id = r.experiment_id
where t.teacher_name = '张sir'
order by e.experiment_id;
""",
            tests=mk_tests(
                lab_setup(),
                lab_setup(
                    """
insert into students values ('S5', '小刘', '信安2301', '信安');
insert into experiment_records values ('S5', 'E3', 'T1', 100, 99);
"""
                ),
                lab_setup(
                    "delete from experiment_records where teacher_id = 'T1' and experiment_id = 'E4';"
                ),
            ),
        )
    )

    specs.append(
        ProblemSpec(
            slug="review-39-lab-senior-teachers-without-records",
            title="A卷：没有带过实验课的高工",
            difficulty="中等",
            tags=["NOT EXISTS", "Subquery", "Exam"],
            statement="""查询没有带过实验课的、职称为 `高工` 的实验师。

输出 `teacher_id,teacher_name`，按 `teacher_id` 升序。""",
            schema="lab_teachers, experiment_records",
            solution_sql="""
select t.teacher_id, t.teacher_name
from lab_teachers t
where t.title = '高工'
  and not exists (
    select 1
    from experiment_records r
    where r.teacher_id = t.teacher_id
  )
order by t.teacher_id;
""",
            tests=mk_tests(
                lab_setup(),
                lab_setup(
                    "insert into experiment_records values ('S4', 'E4', 'T3', 80, 86);"
                ),
                lab_setup("delete from experiment_records where teacher_id = 'T1';"),
            ),
        )
    )

    specs.append(
        ProblemSpec(
            slug="review-40-lab-teachers-all-category-c",
            title="A卷：指导过全部 C 类实验的实验师",
            difficulty="困难",
            tags=["Division", "NOT EXISTS", "Subquery", "Exam"],
            statement="""查询指导过实验类别为 `C类` 的所有实验的实验师编号。

这是典型“除法思想”题：对每个实验师，不能存在某个 `C类` 实验是他没有指导过的。

输出 `teacher_id`，按 `teacher_id` 升序。""",
            schema="lab_teachers, experiments, experiment_records",
            solution_sql="""
select t.teacher_id
from lab_teachers t
where not exists (
  select 1
  from experiments e
  where e.category = 'C类'
    and not exists (
      select 1
      from experiment_records r
      where r.teacher_id = t.teacher_id
        and r.experiment_id = e.experiment_id
    )
)
order by t.teacher_id;
""",
            tests=mk_tests(
                lab_setup(),
                lab_setup(
                    """
insert into students values ('S5', '小刘', '信安2301', '信安');
insert into experiment_records values ('S5', 'E3', 'T5', 100, 90);
"""
                ),
                lab_setup(
                    "delete from experiment_records where teacher_id = 'T2' and experiment_id = 'E3';"
                ),
            ),
        )
    )

    specs.append(
        ProblemSpec(
            slug="review-41-lab-students-at-least-two",
            title="A卷：至少做过 2 个实验的学生",
            difficulty="简单",
            tags=["GROUP BY", "HAVING", "Exam"],
            statement="""查询至少做过 2 个实验的学生。

输出 `student_id,experiment_count`，按 `student_id` 升序。""",
            schema="students, experiment_records",
            solution_sql="""
select student_id, count(*) as experiment_count
from experiment_records
group by student_id
having count(*) >= 2
order by student_id;
""",
            tests=mk_tests(
                lab_setup(),
                lab_setup(
                    "insert into experiment_records values ('S4', 'E1', 'T3', 90, 82);"
                ),
                lab_setup(
                    "delete from experiment_records where student_id = 'S3' and experiment_id = 'E4';"
                ),
            ),
        )
    )

    specs.append(
        ProblemSpec(
            slug="review-42-lab-overtime-score-update",
            title="A卷：超时完成实验扣分",
            difficulty="中等",
            tags=["UPDATE", "JOIN", "Exam"],
            statement="""将超时完成的实验成绩扣掉 10 分。

超时定义为 `experiment_records.actual_minutes > experiments.required_minutes`。只需要修改数据，不需要输出查询结果。""",
            schema="experiments, experiment_records",
            solution_sql="""
update experiment_records r
set score = score - 10
from experiments e
where e.experiment_id = r.experiment_id
  and r.actual_minutes > e.required_minutes;
""",
            judge_sql="""
select student_id, experiment_id, teacher_id, actual_minutes, score
from experiment_records
order by student_id, experiment_id;
""",
            tests=mk_tests(
                lab_setup(),
                lab_setup(
                    "update experiment_records set actual_minutes = 61, score = 100 where student_id = 'S4' and experiment_id = 'E5';"
                ),
                lab_setup(
                    "update experiment_records set actual_minutes = experiments.required_minutes from experiments where experiments.experiment_id = experiment_records.experiment_id;"
                ),
            ),
        )
    )

    specs.append(
        ProblemSpec(
            slug="review-43-lab-lowest-score-records",
            title="A卷：查询最低成绩实验记录",
            difficulty="中等",
            tags=["Subquery", "Exam"],
            statement="""查询实验成绩最低的实验记录；若最低分有多条，需要全部输出。

输出 `student_id,experiment_id,teacher_id,actual_minutes,score`，按 `student_id,experiment_id` 升序。""",
            schema="experiment_records",
            solution_sql="""
select student_id, experiment_id, teacher_id, actual_minutes, score
from experiment_records
where score = (select min(score) from experiment_records)
order by student_id, experiment_id;
""",
            tests=mk_tests(
                lab_setup(),
                lab_setup(
                    "update experiment_records set score = 59 where student_id = 'S2' and experiment_id = 'E3';"
                ),
                lab_setup(
                    "update experiment_records set score = 100 where student_id = 'S3' and experiment_id = 'E2';"
                ),
            ),
        )
    )

    specs.append(
        ProblemSpec(
            slug="review-44-lab-teacher-student-count",
            title="A卷：统计每个实验师带过的学生人数",
            difficulty="中等",
            tags=["GROUP BY", "JOIN", "Exam"],
            statement="""查询每个实验师所带实验的累计学生人数，同一学生被同一实验师带过多次只算 1 人。

只输出带过实验的实验师。输出 `teacher_id,student_count`，按 `student_count` 降序、`teacher_id` 升序。""",
            schema="lab_teachers, experiment_records",
            solution_sql="""
select t.teacher_id,
       count(distinct r.student_id) as student_count
from lab_teachers t
join experiment_records r on r.teacher_id = t.teacher_id
group by t.teacher_id
order by student_count desc, t.teacher_id;
""",
            tests=mk_tests(
                lab_setup(),
                lab_setup(
                    "insert into experiment_records values ('S4', 'E1', 'T1', 90, 81);"
                ),
                lab_setup("delete from experiment_records where teacher_id = 'T5';"),
            ),
        )
    )

    specs.append(
        ProblemSpec(
            slug="review-45-lab-student-summary-view",
            title="A卷：建立学生实验统计视图 VW1",
            difficulty="中等",
            tags=["VIEW", "GROUP BY", "Exam"],
            statement="""建立视图 `vw1`，统计每个学生的实验信息。

视图必须包含以下列：

| 列名 | 含义 |
| --- | --- |
| `student_id` | 学号 |
| `student_name` | 姓名 |
| `total_minutes` | 累计实验时长；没有实验记录时为 0 |
| `avg_score` | 平均成绩，保留两位小数；没有实验记录时为 NULL |

结果不需要在用户 SQL 中查询，只需要创建视图。""",
            schema="students, experiment_records",
            solution_sql="""
create view vw1 as
select s.student_id,
       s.student_name,
       coalesce(sum(r.actual_minutes), 0)::integer as total_minutes,
       round(avg(r.score), 2)::numeric(10,2) as avg_score
from students s
left join experiment_records r on r.student_id = s.student_id
group by s.student_id, s.student_name;
""",
            judge_sql="""
select student_id, student_name, total_minutes, avg_score
from vw1
order by student_id;
""",
            tests=mk_tests(
                lab_setup(),
                lab_setup(
                    "insert into students values ('S5', '小刘', '信安2301', '信安');"
                ),
                lab_setup("delete from experiment_records where student_id = 'S4';"),
            ),
        )
    )

    specs.append(
        ProblemSpec(
            slug="review-46-spj-exists-heavy-red-suppliers",
            title="EXISTS：供应过大批量红色零件的供应商",
            difficulty="中等",
            tags=["EXISTS", "SPJ", "Subquery"],
            statement="""基于 SPJ 数据库，查询至少供应过一次红色零件且供应数量不少于 200 的供应商。

建议使用 `EXISTS` 相关子查询。

输出 `sno,sname`，按 `sno` 升序。""",
            schema="s(sno,sname,status,city), p(pno,pname,color,weight), spj(sno,pno,jno,qty)",
            solution_sql="""
select s.sno, s.sname
from s
where exists (
  select 1
  from spj
  join p on p.pno = spj.pno
  where spj.sno = s.sno
    and p.color = '红'
    and spj.qty >= 200
)
order by s.sno;
""",
            tests=mk_tests(
                spj_setup(),
                spj_setup("insert into spj values ('S2', 'P4', 'J2', 250);"),
                spj_setup(
                    "delete from spj where sno in ('S1', 'S4', 'S5') and pno in ('P1', 'P6');"
                ),
            ),
        )
    )

    specs.append(
        ProblemSpec(
            slug="review-47-spj-in-red-part-suppliers",
            title="IN：供应过红色零件的供应商",
            difficulty="简单",
            tags=["IN", "SPJ", "Subquery"],
            statement="""基于 SPJ 数据库，查询供应过任意红色零件的供应商编号。

建议使用 `IN` 子查询：先找出红色零件号，再匹配供应情况。

输出 `sno`，去重后按 `sno` 升序。""",
            schema="p(pno,pname,color,weight), spj(sno,pno,jno,qty)",
            solution_sql="""
select distinct sno
from spj
where pno in (
  select pno
  from p
  where color = '红'
)
order by sno;
""",
            tests=mk_tests(
                spj_setup(),
                spj_setup("insert into spj values ('S2', 'P4', 'J2', 250);"),
                spj_setup("update p set color = '绿' where pno in ('P1', 'P6');"),
            ),
        )
    )

    specs.append(
        ProblemSpec(
            slug="review-48-lab-not-exists-no-b-experiment",
            title="NOT EXISTS：没有做过 B 类实验的学生",
            difficulty="中等",
            tags=["NOT EXISTS", "Subquery", "Exam"],
            statement="""实验管理数据库包含实验师、学生、实验、实验记录四张表。

查询没有做过任何 `B类` 实验的学生。

建议使用 `NOT EXISTS`，注意学生没有实验记录时也应视为“没有做过 B 类实验”。

输出 `student_id,student_name`，按 `student_id` 升序。""",
            schema="students, experiments, experiment_records",
            solution_sql="""
select s.student_id, s.student_name
from students s
where not exists (
  select 1
  from experiment_records r
  join experiments e on e.experiment_id = r.experiment_id
  where r.student_id = s.student_id
    and e.category = 'B类'
)
order by s.student_id;
""",
            tests=mk_tests(
                lab_setup(),
                lab_setup(
                    "insert into experiment_records values ('S4', 'E4', 'T3', 90, 82);"
                ),
                lab_setup(
                    "delete from experiment_records where student_id = 'S3' and experiment_id = 'E4';"
                ),
            ),
        )
    )

    specs.append(
        ProblemSpec(
            slug="review-49-spj-not-in-no-tianjin-supplier-projects",
            title="NOT IN：没有使用天津供应商的工程",
            difficulty="中等",
            tags=["NOT IN", "SPJ", "Subquery"],
            statement="""基于 SPJ 数据库，查询没有使用过天津供应商供货的工程。

建议使用 `NOT IN` 子查询。没有任何供货记录的工程也应输出。

输出 `jno,jname`，按 `jno` 升序。""",
            schema="s(sno,sname,status,city), j(jno,jname,city), spj(sno,pno,jno,qty)",
            solution_sql="""
select j.jno, j.jname
from j
where j.jno not in (
  select spj.jno
  from spj
  join s on s.sno = spj.sno
  where s.city = '天津'
)
order by j.jno;
""",
            tests=mk_tests(
                spj_setup(),
                spj_setup("insert into spj values ('S1', 'P1', 'J5', 20);"),
                spj_setup("update s set city = '北京' where sno = 'S1';"),
            ),
        )
    )

    specs.append(
        ProblemSpec(
            slug="review-50-lab-division-students-all-c",
            title="除法思想：完成全部 C 类实验的学生",
            difficulty="困难",
            tags=["Division", "NOT EXISTS", "Subquery", "Exam"],
            statement="""查询完成过全部 `C类` 实验的学生。

这是典型“除法思想”题：对每个学生，不能存在某个 `C类` 实验是他没有完成记录的。

输出 `student_id,student_name`，按 `student_id` 升序。""",
            schema="students, experiments, experiment_records",
            solution_sql="""
select s.student_id, s.student_name
from students s
where not exists (
  select 1
  from experiments e
  where e.category = 'C类'
    and not exists (
      select 1
      from experiment_records r
      where r.student_id = s.student_id
        and r.experiment_id = e.experiment_id
    )
)
order by s.student_id;
""",
            tests=mk_tests(
                lab_setup(),
                lab_setup(
                    """
insert into experiment_records values
  ('S4', 'E2', 'T2', 110, 80),
  ('S4', 'E3', 'T2', 90, 83);
"""
                ),
                lab_setup(
                    "delete from experiment_records where student_id = 'S1' and experiment_id = 'E3';"
                ),
            ),
        )
    )

    by_slug = {spec.slug: spec for spec in specs}
    missing = [slug for slug in FOCUSED_REVIEW_SLUGS if slug not in by_slug]
    if missing:
        raise RuntimeError(f"Focused review slug(s) are missing: {', '.join(missing)}")
    return [by_slug[slug] for slug in FOCUSED_REVIEW_SLUGS]


def write_problem(problem: ProblemSpec, output: Path = OUTPUT) -> None:
    problem_dir = output / "problems" / problem.slug
    if problem_dir.exists():
        shutil.rmtree(problem_dir)
    (problem_dir / "tests").mkdir(parents=True)

    metadata = {
        "slug": problem.slug,
        "title": problem.title,
        "difficulty": problem.difficulty,
        "tags": problem.tags,
        "checker": problem.checker,
        "ordered": problem.ordered,
        "time_limit_ms": problem.time_limit_ms,
    }
    (problem_dir / "problem.yaml").write_text(
        yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    (problem_dir / "statement.md").write_text(
        problem.statement.strip() + "\n", encoding="utf-8"
    )
    schema_md = derive_schema_ddl(problem.tests[0].setup_sql) if problem.tests else ""
    if not schema_md:
        schema_md = problem.schema.strip()
    (problem_dir / "schema.md").write_text(schema_md + "\n", encoding="utf-8")

    for test in problem.tests:
        test_dir = problem_dir / "tests" / test.name
        test_dir.mkdir()
        (test_dir / "setup.sql").write_text(
            test.setup_sql.strip() + "\n", encoding="utf-8"
        )
        (test_dir / "judge.sql").write_text(
            problem.judge_sql.strip() + "\n", encoding="utf-8"
        )
        (test_dir / "expected.csv").write_text(
            expected_output(problem, test), encoding="utf-8"
        )

    zip_path = output / "zips" / f"{problem.slug}.zip"
    with ZipFile(zip_path, "w", compression=ZIP_DEFLATED) as zf:
        for path in sorted(problem_dir.rglob("*")):
            if path.is_file():
                arcname = path.relative_to(problem_dir).as_posix()
                info = ZipInfo(arcname, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = ZIP_DEFLATED
                zf.writestr(info, path.read_bytes())


def write_readme(specs: list[ProblemSpec], output: Path = OUTPUT) -> None:
    lines = [
        "# Review 2026 SQL Problem Packages",
        "",
        "These packages are the high-priority SQL subset generated from the exam review scope, `program.md`, the referenced SPJ/PDF lab materials, and the 2025 A paper.",
        "",
        "Kept topics: JOIN, nested subquery, GROUP BY/HAVING, EXISTS, NOT EXISTS, IN/NOT IN, division-style queries, UPDATE, and VIEW.",
        "",
        "Import zip files from `examples/review-2026/zips/` in the admin page. Each problem has one public sample and two hidden test cases.",
        "",
        "## Regenerating",
        "",
        "Run the generator from the repository root with the backend environment active:",
        "",
        "```bash",
        "source backend/.venv/bin/activate",
        "export ADMIN_DATABASE_URL='postgresql:///postgres?host=/tmp'",
        "python tools/generate_review_problems.py",
        "```",
        "",
        "The script reads `ADMIN_DATABASE_URL` from the process environment (it does not load the repository `.env`), creates and drops random `oj_pkg_*` databases, and replaces the entire `examples/review-2026/` directory only after every package has been generated successfully. Back up any manual edits before running it. The configured database role must be allowed to create, terminate connections to, and drop those temporary databases.",
        "",
        "Replacing an existing output uses the operating system's atomic directory exchange on macOS or Linux. If that operation is unavailable or fails, the old output remains in place and the completed hidden staging directory is retained; the generator never falls back to a non-atomic two-step replacement.",
        "",
        "| # | Slug | Title | Tags |",
        "| --- | --- | --- | --- |",
    ]
    for index, spec in enumerate(specs, start=1):
        lines.append(
            f"| {index} | `{spec.slug}` | {spec.title} | {', '.join(spec.tags)} |"
        )
    (output / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def validate_output_target(output: Path = OUTPUT) -> None:
    examples_dir = ROOT / "examples"
    if (
        examples_dir.is_symlink()
        or output.is_symlink()
        or output.parent != examples_dir
    ):
        raise RuntimeError(
            "Refusing to replace a generated-output path through a symlink"
        )
    if output.exists() and not output.is_dir():
        raise RuntimeError("Generated-output target must be a directory")


def _atomic_exchange_directories(first: Path, second: Path) -> None:
    """Atomically swap two directory names without an observable missing path."""
    library = ctypes.CDLL(None, use_errno=True)
    first_bytes = os.fsencode(first)
    second_bytes = os.fsencode(second)

    if sys.platform == "darwin":
        try:
            exchange = library.renamex_np
        except AttributeError as exc:
            raise RuntimeError(
                "This macOS version does not provide atomic directory exchange"
            ) from exc
        exchange.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        exchange.restype = ctypes.c_int
        result = exchange(first_bytes, second_bytes, 0x00000002)  # RENAME_SWAP
    elif sys.platform.startswith("linux"):
        try:
            exchange = library.renameat2
        except AttributeError as exc:
            raise RuntimeError(
                "This Linux runtime does not provide atomic directory exchange"
            ) from exc
        exchange.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        exchange.restype = ctypes.c_int
        result = exchange(
            -100,  # AT_FDCWD
            first_bytes,
            -100,
            second_bytes,
            0x00000002,  # RENAME_EXCHANGE
        )
    else:
        raise RuntimeError(
            f"Atomic directory exchange is not supported on {sys.platform}"
        )

    if result != 0:
        error_number = ctypes.get_errno() or errno.EIO
        raise OSError(
            error_number,
            "Could not atomically exchange generated-output directories: "
            f"{os.strerror(error_number)}",
            f"{first} <-> {second}",
        )


def install_generated_output(staging: Path, output: Path = OUTPUT) -> None:
    if staging == output:
        raise RuntimeError("Generated output and staging must be different paths")
    if staging.is_symlink() or not staging.is_dir():
        raise RuntimeError("Generated-output staging path must be a directory")
    if staging.parent != output.parent:
        raise RuntimeError("Generated output and staging must share a parent directory")
    if output.is_symlink():
        raise RuntimeError("Generated-output target must not be a symlink")

    if not output.exists():
        staging.rename(output)
        return
    if not output.is_dir():
        raise RuntimeError("Generated-output target must be a directory")

    # After the exchange, `output` is the complete new tree and `staging` is
    # the old tree. The namespace operation itself never exposes a missing
    # output path, including if the process is terminated immediately after it.
    _atomic_exchange_directories(staging, output)
    try:
        shutil.rmtree(staging)
    except Exception as cleanup_error:
        print(
            f"warning: generated output installed, but old output {staging} "
            f"could not be removed: {cleanup_error}",
            file=sys.stderr,
        )


def main() -> None:
    validate_output_target(OUTPUT)
    specs = problem_specs()
    staging = OUTPUT.parent / f".{OUTPUT.name}.staging-{uuid.uuid4().hex}"
    generation_error: BaseException | None = None
    staging_created = False
    staging_complete = False
    output_installed = False
    try:
        # The exclusive root mkdir prevents a UUID collision from reusing or
        # later deleting a retained staging directory from another run.
        staging.mkdir()
        staging_created = True
        (staging / "problems").mkdir()
        (staging / "zips").mkdir()
        for index, problem in enumerate(specs, start=1):
            print(f"[{index:02d}/{len(specs)}] {problem.slug}")
            write_problem(problem, staging)
        write_readme(specs, staging)
        staging_complete = True
        install_generated_output(staging, OUTPUT)
        output_installed = True
    except BaseException as exc:
        generation_error = exc
        raise
    finally:
        # If installation succeeded, a remaining staging path is the old tree
        # and install_generated_output has already warned about its cleanup.
        # Do not turn a complete new output into a misleading failure.
        if staging_created and staging.exists() and not output_installed:
            if generation_error is not None and staging_complete:
                print(
                    f"warning: complete generated output retained at {staging}",
                    file=sys.stderr,
                )
            else:
                try:
                    shutil.rmtree(staging)
                except Exception as cleanup_error:
                    if generation_error is None:
                        raise
                    print(
                        f"warning: failed to remove staging directory {staging}: "
                        f"{cleanup_error}",
                        file=sys.stderr,
                    )
    print(f"Generated {len(specs)} problems under {OUTPUT}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"generation failed: {exc}", file=sys.stderr)
        raise
