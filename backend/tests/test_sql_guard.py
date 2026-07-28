import pytest

from app.services.sql_guard import (
    UnsafeSqlError,
    is_streamable_query,
    split_sql_statements,
    validate_user_sql,
)


@pytest.mark.parametrize(
    "sql",
    [
        "CREATE DATABASE bad;",
        "ALTER DATABASE current_database RENAME TO stolen;",
        "drop role x;",
        "ALTER USER current_user PASSWORD 'stolen';",
        "ALTER SYSTEM SET shared_buffers = '1GB';",
        "COPY t TO '/tmp/a.csv';",
        "COPY t TO PROGRAM 'id';",
        "COPY t TO STDOUT;",
        "CREATE EXTENSION file_fdw;",
        "CREATE TRUSTED PROCEDURAL LANGUAGE dangerous HANDLER handler;",
        "CREATE RULE dangerous AS ON INSERT TO t DO ALSO NOTIFY x;",
        "CREATE SUBSCRIPTION remote CONNECTION 'host=example.com' PUBLICATION p;",
        "CREATE SERVER remote FOREIGN DATA WRAPPER postgres_fdw;",
        "IMPORT FOREIGN SCHEMA public FROM SERVER remote INTO public;",
        "SET ROLE postgres;",
        "SET LOCAL ROLE postgres;",
        "RESET/**/ROLE;",
        "DISCARD ALL;",
        "SET SESSION AUTHORIZATION postgres;",
        "SELECT set_config('role', 'none', false);",
        r"""SELECT U&"set\005fconfig"('role', 'none', false);""",
        r"""SELECT U&"pg\005fread\005ffile"('/etc/passwd') UESCAPE '\';""",
        "select pg_read_file('/etc/passwd');",
        "select pg_catalog.\"pg_read_binary_file\"('/etc/passwd');",
        'select pg_catalog./**/"pg_terminate_backend"(123);',
        "select pg_advisory_lock(1);",
        "select pg_try_advisory_xact_lock(1);",
        "select pg_notify('channel', 'payload');",
        "select pg_logical_emit_message(false, 'prefix', 'content');",
        "select query_to_xml($$select set_config('role', 'none', false)$$, false, true, '');",
        "select query_to_xmlschema('select 1', false, true, '');",
        "select query_to_xml_and_xmlschema('select 1', false, true, '');",
        "select * from dblink('host=127.0.0.1', $$select set_config('role', 'none', false)$$) as t(x text);",
        "select * from crosstab($$select set_config('role', 'none', false)$$) as t(x text);",
        "select * from crosstab2($$select pg_logical_emit_message(false, 'x', 'y')$$);",
        "select * from connectby('tree', 'id', 'parent_id', '1', 0, $$x = set_config('role','none',false)$$) as t(x text);",
        "select * from ts_stat($$select to_tsvector(set_config('role', 'none', false))$$);",
        "select ts_rewrite(to_tsquery('a'), $$select to_tsquery('a'), to_tsquery(set_config('role','none',false))$$);",
        "NOTIFY channel, 'payload';",
        "REVOKE CONNECT ON DATABASE current_database FROM app_user;",
        "GRANT some_role TO current_user;",
        "COMMENT ON DATABASE current_database IS 'not-a-judge-database';",
        "SECURITY LABEL ON DATABASE current_database IS 'untrusted';",
        "SECURITY LABEL FOR dummy_provider ON DATABASE current_database IS 'untrusted';",
        "DO $$ BEGIN EXECUTE 'RESET ROLE'; END $$;",
        "CREATE OR REPLACE FUNCTION escape() RETURNS void LANGUAGE plpgsql AS $$ BEGIN EXECUTE 'RESET ROLE'; END $$;",
        "CREATE AGGREGATE sum2(integer) (SFUNC = int4pl, STYPE = integer);",
        "CREATE OPERATOR === (LEFTARG = integer, RIGHTARG = integer, FUNCTION = int4eq);",
        "CREATE CAST (integer AS text) WITH FUNCTION pg_catalog.set_config(text, text, boolean);",
        "CREATE TRANSFORM FOR jsonb LANGUAGE SQL (FROM SQL WITH FUNCTION pg_catalog.set_config(text, text, boolean));",
        "CREATE ACCESS METHOD dangerous TYPE TABLE HANDLER heap_tableam_handler;",
        "CREATE AGGREGATE escape(text, boolean) (SFUNC = pg_catalog.set_config, STYPE = text);",
        "PREPARE TRANSACTION 'escape';",
        "ROLLBACK PREPARED 'escape';",
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
        grant select on answer to public;
        revoke select on answer from public;
        select * from answer;
        """
    )


def test_dangerous_words_inside_literals_and_comments_are_not_false_positives():
    validate_user_sql(
        """
        -- RESET ROLE; SELECT pg_read_file('/etc/passwd');
        select 'DROP DATABASE x', $$DO $$, E'COPY t TO PROGRAM \\'x\\'';
        /* nested /* ALTER USER me */ comment */
        create table ordinary_table(id int, note text);
        """
    )


def test_statement_splitter_preserves_quoted_and_commented_semicolons():
    statements = split_sql_statements(
        "SELECT ';' AS a; SELECT E'escaped\\';semicolon' AS b; "
        "SELECT $tag$dollar;semicolon$tag$ AS c; /* ; */ SELECT 4"
    )

    assert len(statements) == 4
    assert "dollar;semicolon" in statements[2]
    assert statements[3].endswith("SELECT 4")


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        ("SELECT 1", True),
        ("VALUES (1)", True),
        ("TABLE answer", True),
        ("WITH x AS (SELECT 1) SELECT * FROM x", True),
        ("INSERT INTO answer VALUES (1)", False),
        ("WITH x AS (SELECT 1) UPDATE answer SET id = 2", False),
    ],
)
def test_streamable_query_detection(sql, expected):
    assert is_streamable_query(sql) is expected
