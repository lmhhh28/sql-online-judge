from __future__ import annotations

from dataclasses import dataclass


class UnsafeSqlError(ValueError):
    pass


@dataclass(frozen=True)
class _Token:
    kind: str
    value: str


_FORBIDDEN_FUNCTIONS = {
    "connectby": "executing dynamically constructed SQL is not allowed",
    "crosstab": "executing SQL from a string is not allowed",
    "lo_export": "large-object server file access is not allowed",
    "lo_import": "large-object server file access is not allowed",
    "pg_cancel_backend": "controlling PostgreSQL backends is not allowed",
    "pg_log_backend_memory_contexts": "inspecting PostgreSQL backends is not allowed",
    "pg_ls_archive_statusdir": "server file access is not allowed",
    "pg_ls_dir": "server file access is not allowed",
    "pg_ls_logicalmapdir": "server file access is not allowed",
    "pg_ls_logicalsnapdir": "server file access is not allowed",
    "pg_ls_logdir": "server file access is not allowed",
    "pg_ls_replslotdir": "server file access is not allowed",
    "pg_ls_tmpdir": "server file access is not allowed",
    "pg_ls_waldir": "server file access is not allowed",
    "pg_notify": "server notifications are not allowed",
    "pg_logical_emit_message": "writing logical WAL messages is not allowed",
    "pg_read_binary_file": "server file access is not allowed",
    "pg_read_file": "server file access is not allowed",
    "pg_reload_conf": "server configuration control is not allowed",
    "pg_rotate_logfile": "server log control is not allowed",
    "pg_stat_file": "server file access is not allowed",
    "pg_terminate_backend": "controlling PostgreSQL backends is not allowed",
    "query_to_xml": "executing SQL from a string is not allowed",
    "query_to_xml_and_xmlschema": "executing SQL from a string is not allowed",
    "query_to_xmlschema": "executing SQL from a string is not allowed",
    "set_config": "changing protected session settings is not allowed",
    "ts_rewrite": "executing SQL from a string is not allowed",
    "ts_stat": "executing SQL from a string is not allowed",
}


def _scan_single_quoted(sql: str, start: int, backslash_escapes: bool = False) -> int:
    """Return the first position after a PostgreSQL single-quoted string."""
    index = start + 1
    while index < len(sql):
        if backslash_escapes and sql[index] == "\\" and index + 1 < len(sql):
            index += 2
            continue
        if sql[index] == "'":
            if index + 1 < len(sql) and sql[index + 1] == "'":
                index += 2
                continue
            return index + 1
        index += 1
    return len(sql)


def _scan_quoted_identifier(sql: str, start: int) -> tuple[int, str]:
    index = start + 1
    value: list[str] = []
    while index < len(sql):
        if sql[index] == '"':
            if index + 1 < len(sql) and sql[index + 1] == '"':
                value.append('"')
                index += 2
                continue
            return index + 1, "".join(value)
        value.append(sql[index])
        index += 1
    return len(sql), "".join(value)


def _dollar_quote_delimiter(sql: str, start: int) -> str | None:
    if sql[start] != "$":
        return None
    end = sql.find("$", start + 1)
    if end < 0:
        return None
    tag = sql[start + 1 : end]
    if tag and (
        not (tag[0].isalpha() or tag[0] == "_")
        or not all(ch.isalnum() or ch == "_" for ch in tag)
    ):
        return None
    return sql[start : end + 1]


def _tokenize(sql: str) -> list[_Token]:
    """Tokenize enough PostgreSQL syntax to inspect commands without regex bypasses.

    Comments and string contents are deliberately omitted. Quoted identifiers are
    retained so calls such as ``"pg_read_file"(...)`` are still recognized.
    """
    tokens: list[_Token] = []
    index = 0
    length = len(sql)

    while index < length:
        char = sql[index]
        if char.isspace():
            index += 1
            continue

        if sql.startswith("--", index):
            newline = sql.find("\n", index + 2)
            index = length if newline < 0 else newline + 1
            continue

        if sql.startswith("/*", index):
            depth = 1
            index += 2
            while index < length and depth:
                if sql.startswith("/*", index):
                    depth += 1
                    index += 2
                elif sql.startswith("*/", index):
                    depth -= 1
                    index += 2
                else:
                    index += 1
            continue

        # PostgreSQL supports E'...', N'...', B'...', X'...', and U&'...'.
        if char in "eEnNbBxX" and index + 1 < length and sql[index + 1] == "'":
            index = _scan_single_quoted(sql, index + 1, backslash_escapes=char in "eE")
            tokens.append(_Token("string", ""))
            continue
        if (
            char in "uU"
            and index + 2 < length
            and sql[index + 1] == "&"
            and sql[index + 2] == "'"
        ):
            index = _scan_single_quoted(sql, index + 2)
            tokens.append(_Token("string", ""))
            continue
        if (
            char in "uU"
            and index + 2 < length
            and sql[index + 1] == "&"
            and sql[index + 2] == '"'
        ):
            index, value = _scan_quoted_identifier(sql, index + 2)
            tokens.append(_Token("unicode_identifier", value))
            continue
        if char == "'":
            index = _scan_single_quoted(sql, index)
            tokens.append(_Token("string", ""))
            continue

        if char == '"':
            index, value = _scan_quoted_identifier(sql, index)
            tokens.append(_Token("identifier", value.casefold()))
            continue

        delimiter = _dollar_quote_delimiter(sql, index) if char == "$" else None
        if delimiter is not None:
            body_start = index + len(delimiter)
            body_end = sql.find(delimiter, body_start)
            index = length if body_end < 0 else body_end + len(delimiter)
            tokens.append(_Token("string", ""))
            continue

        if char.isalpha() or char == "_":
            end = index + 1
            while end < length and (sql[end].isalnum() or sql[end] in "_$"):
                end += 1
            tokens.append(_Token("word", sql[index:end].casefold()))
            index = end
            continue

        tokens.append(_Token("symbol", char))
        index += 1

    return tokens


def split_sql_statements(sql: str) -> list[str]:
    """Split top-level statements without treating quoted semicolons as delimiters."""
    statements: list[str] = []
    start = 0
    index = 0
    length = len(sql)
    while index < length:
        char = sql[index]
        if sql.startswith("--", index):
            newline = sql.find("\n", index + 2)
            index = length if newline < 0 else newline + 1
            continue
        if sql.startswith("/*", index):
            depth = 1
            index += 2
            while index < length and depth:
                if sql.startswith("/*", index):
                    depth += 1
                    index += 2
                elif sql.startswith("*/", index):
                    depth -= 1
                    index += 2
                else:
                    index += 1
            continue
        if char in "eEnNbBxX" and index + 1 < length and sql[index + 1] == "'":
            index = _scan_single_quoted(sql, index + 1, backslash_escapes=char in "eE")
            continue
        if (
            char in "uU"
            and index + 2 < length
            and sql[index + 1] == "&"
            and sql[index + 2] == "'"
        ):
            index = _scan_single_quoted(sql, index + 2)
            continue
        if char == "'":
            index = _scan_single_quoted(sql, index)
            continue
        if char == '"':
            index, _ = _scan_quoted_identifier(sql, index)
            continue
        delimiter = _dollar_quote_delimiter(sql, index) if char == "$" else None
        if delimiter is not None:
            body_start = index + len(delimiter)
            body_end = sql.find(delimiter, body_start)
            index = length if body_end < 0 else body_end + len(delimiter)
            continue
        if char == ";":
            statement = sql[start:index].strip()
            if statement and _tokenize(statement):
                statements.append(statement)
            start = index + 1
        index += 1

    statement = sql[start:].strip()
    if statement and _tokenize(statement):
        statements.append(statement)
    return statements


def is_streamable_query(sql: str) -> bool:
    """Return whether a statement can normally be declared as a server cursor."""
    top_level_words: list[str] = []
    depth = 0
    for token in _tokenize(sql):
        if token.kind == "symbol":
            if token.value == "(":
                depth += 1
            elif token.value == ")" and depth:
                depth -= 1
        elif token.kind == "word" and depth == 0:
            top_level_words.append(token.value)

    if not top_level_words:
        return False
    if top_level_words[0] in {"select", "table", "values"}:
        return True
    if top_level_words[0] == "with":
        return any(
            word in {"select", "table", "values"}
            for word in reversed(top_level_words[1:])
        )
    return False


def _statements(tokens: list[_Token]) -> list[list[_Token]]:
    statements: list[list[_Token]] = []
    current: list[_Token] = []
    for token in tokens:
        if token.kind == "symbol" and token.value == ";":
            if current:
                statements.append(current)
                current = []
        else:
            current.append(token)
    if current:
        statements.append(current)
    return statements


def _words(statement: list[_Token]) -> list[str]:
    return [token.value for token in statement if token.kind == "word"]


def _starts_with(words: list[str], *prefix: str) -> bool:
    return words[: len(prefix)] == list(prefix)


def validate_user_sql(sql: str) -> None:
    tokens = _tokenize(sql)

    if any(token.kind == "unicode_identifier" for token in tokens):
        # U&"..." can encode every blocked function name (and supports a
        # caller-selected UESCAPE character). Rejecting this rare spelling is
        # safer than duplicating PostgreSQL's Unicode identifier decoder.
        raise UnsafeSqlError("Unicode-escaped identifiers are not allowed")

    for token in tokens:
        if token.kind not in {"word", "identifier"}:
            continue
        message = _FORBIDDEN_FUNCTIONS.get(token.value)
        if token.value.startswith(("pg_advisory_", "pg_try_advisory_")):
            message = "advisory locks are not allowed"
        if token.value.startswith("dblink"):
            message = "external database connections are not allowed"
        if token.value.startswith("crosstab"):
            message = "executing SQL from a string is not allowed"
        # Match names anywhere in executable SQL, not only function-call syntax.
        # PostgreSQL DDL can bind a function indirectly through SFUNC,
        # PROCEDURE, CAST, operator, transform, and similar clauses.
        if message:
            raise UnsafeSqlError(message)

    for statement in _statements(tokens):
        words = _words(statement)
        if not words:
            continue

        first = words[0]
        if first in {"set", "reset", "discard"}:
            raise UnsafeSqlError("changing session state is not allowed")
        if first == "copy":
            raise UnsafeSqlError("COPY is not allowed; use INSERT or SELECT instead")
        if (first == "prepare" and words[1:2] == ["transaction"]) or (
            first in {"commit", "rollback"} and words[1:2] == ["prepared"]
        ):
            raise UnsafeSqlError("prepared transactions are not allowed")
        if first in {"do", "call", "load", "listen", "notify", "unlisten"}:
            raise UnsafeSqlError(f"{first.upper()} is not allowed")
        if _starts_with(words, "drop", "owned") or _starts_with(
            words, "reassign", "owned"
        ):
            raise UnsafeSqlError("changing role-owned objects is not allowed")

        offset = 1
        if first == "create" and words[1:3] == ["or", "replace"]:
            offset = 3
        object_type = words[offset] if len(words) > offset else ""

        if first in {"create", "alter", "drop"} and object_type in {
            "database",
            "role",
            "user",
            "group",
        }:
            raise UnsafeSqlError(
                f"{first.upper()} {object_type.upper()} is not allowed"
            )
        if first == "alter" and object_type == "system":
            raise UnsafeSqlError("ALTER SYSTEM is not allowed")
        if first in {"create", "alter", "drop"} and object_type in {
            "extension",
            "language",
            "tablespace",
        }:
            raise UnsafeSqlError(f"{object_type.upper()} management is not allowed")
        if (
            first in {"create", "alter", "drop"}
            and "language" in words[offset : offset + 3]
        ):
            raise UnsafeSqlError("LANGUAGE management is not allowed")
        if first == "create" and object_type in {"function", "procedure", "rule"}:
            raise UnsafeSqlError("creating executable server code is not allowed")
        if first == "create" and (
            object_type in {"aggregate", "operator", "cast", "transform", "conversion"}
            or words[offset : offset + 2] == ["access", "method"]
        ):
            raise UnsafeSqlError("creating executable SQL wrappers is not allowed")
        if first in {"create", "alter", "drop"} and object_type in {
            "foreign",
            "server",
            "subscription",
        }:
            raise UnsafeSqlError("external database access is not allowed")
        if first == "import" and object_type == "foreign":
            raise UnsafeSqlError("external database access is not allowed")
        if first in {"grant", "revoke"} and ("on" not in words or "database" in words):
            raise UnsafeSqlError("changing database or role grants is not allowed")
        if first == "comment" and words[:3] in (
            ["comment", "on", "role"],
            ["comment", "on", "user"],
        ):
            raise UnsafeSqlError("changing shared role metadata is not allowed")
        if first == "comment" and words[:3] == ["comment", "on", "database"]:
            raise UnsafeSqlError("changing database metadata is not allowed")
        if first == "security" and words[1:2] == ["label"]:
            try:
                on_index = words.index("on", 2)
            except ValueError:
                on_index = -1
            if words[on_index + 1 : on_index + 2] == ["database"]:
                raise UnsafeSqlError("changing database metadata is not allowed")
