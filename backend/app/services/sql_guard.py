import re


class UnsafeSqlError(ValueError):
    pass


BLOCK_PATTERNS: list[tuple[str, str]] = [
    (r"\b(create|drop)\s+database\b", "CREATE/DROP DATABASE is not allowed"),
    (r"\b(create|alter|drop)\s+role\b", "CREATE/ALTER/DROP ROLE is not allowed"),
    (r"\balter\s+system\b", "ALTER SYSTEM is not allowed"),
    (r"\bcreate\s+extension\b", "CREATE EXTENSION is not allowed"),
    (r"\bset\s+role\b", "SET ROLE is not allowed"),
    (r"\bcopy\b[^;]*\bprogram\b", "COPY PROGRAM is not allowed"),
    (r"\bcopy\b[^;]*(from|to)\s+['\"]", "COPY from/to server files is not allowed"),
    (r"\bpg_read_file\s*\(", "pg_read_file is not allowed"),
    (r"\bpg_ls_dir\s*\(", "pg_ls_dir is not allowed"),
    (r"\blo_import\s*\(", "lo_import is not allowed"),
    (r"\blo_export\s*\(", "lo_export is not allowed"),
]


def validate_user_sql(sql: str) -> None:
    for pattern, message in BLOCK_PATTERNS:
        if re.search(pattern, sql, flags=re.IGNORECASE | re.DOTALL):
            raise UnsafeSqlError(message)

