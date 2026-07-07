import pytest

from app.services.sql_guard import UnsafeSqlError, validate_user_sql


@pytest.mark.parametrize(
    "sql",
    [
        "CREATE DATABASE bad;",
        "drop role x;",
        "ALTER SYSTEM SET shared_buffers = '1GB';",
        "COPY t TO '/tmp/a.csv';",
        "COPY t TO PROGRAM 'id';",
        "CREATE EXTENSION file_fdw;",
        "SET ROLE postgres;",
        "select pg_read_file('/etc/passwd');",
    ],
)
def test_blocks_high_risk_sql(sql):
    with pytest.raises(UnsafeSqlError):
        validate_user_sql(sql)


def test_allows_ordinary_ddl_and_dml():
    validate_user_sql(
        """
        create table answer(id int);
        insert into answer values (1);
        update answer set id = 2;
        select * from answer;
        """
    )

