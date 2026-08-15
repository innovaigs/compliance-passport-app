"""
Structural comparison of two SQLite schemas.

Used on the one-time adoption path in database.init_db: a database created
before migrations existed may only be stamped as current if its schema is
genuinely identical to what revision 0001 produces. Stamping an unverified
database would be the application asserting something it has not checked.

The comparison covers every table (excluding SQLite internals and the Alembic
bookkeeping table), and for each table: column name, declared type, nullability,
default, primary-key membership, and every non-autoindex index with its columns.
Ordering is normalised, so a difference reported here is a real difference.
"""

from __future__ import annotations

import sqlite3
from typing import Any, Dict, List

# Excluded from comparison: Alembic's own bookkeeping table, which by definition
# is absent from a pre-Alembic database and present after an upgrade.
IGNORED_TABLES = {"alembic_version"}


def _column_signature(row: tuple) -> str:
    """Renders one PRAGMA table_info row as a stable, comparable string."""
    _cid, name, decl_type, notnull, default, pk = row
    parts = [f"{name}:{decl_type or 'NONE'}"]
    if notnull:
        parts.append("NOT NULL")
    if default is not None:
        parts.append(f"DEFAULT {default}")
    if pk:
        parts.append("PK")
    return " ".join(parts)


def describe_schema(conn: sqlite3.Connection) -> Dict[str, Dict[str, List[str]]]:
    """Returns {table: {"columns": [...], "indexes": [...]}} with everything sorted."""
    cur = conn.cursor()
    tables = sorted(
        r[0]
        for r in cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%'"
        )
        if r[0] not in IGNORED_TABLES
    )

    described: Dict[str, Dict[str, List[str]]] = {}
    for table in tables:
        columns = sorted(
            _column_signature(row)
            for row in cur.execute(f"PRAGMA table_info('{table}')").fetchall()
        )

        # fetchall() before the inner PRAGMA: re-executing on a cursor that is
        # still being iterated truncates the outer result set, which silently
        # drops every index after the first.
        index_rows = cur.execute(f"PRAGMA index_list('{table}')").fetchall()
        indexes = []
        for idx_row in index_rows:
            idx_name = idx_row[1]
            if idx_name.startswith("sqlite_autoindex"):
                continue
            idx_cols = ",".join(
                r[2] for r in cur.execute(f"PRAGMA index_info('{idx_name}')").fetchall()
            )
            indexes.append(f"{idx_name}({idx_cols})")

        described[table] = {"columns": columns, "indexes": sorted(indexes)}

    return described


def diff_schemas(
    expected: Dict[str, Dict[str, List[str]]],
    actual: Dict[str, Dict[str, List[str]]],
    expected_label: str = "revision 0001",
    actual_label: str = "existing database",
) -> List[str]:
    """
    Returns a list of human-readable differences. Empty means identical.

    Every line names the table and the specific column or index involved, so the
    message is actionable without re-running anything.
    """
    differences: List[str] = []

    for table in sorted(set(expected) - set(actual)):
        differences.append(f"table {table!r}: in {expected_label}, missing from {actual_label}")
    for table in sorted(set(actual) - set(expected)):
        differences.append(f"table {table!r}: in {actual_label}, not in {expected_label}")

    for table in sorted(set(expected) & set(actual)):
        for kind, noun in (("columns", "column"), ("indexes", "index")):
            exp = set(expected[table][kind])
            act = set(actual[table][kind])
            for item in sorted(exp - act):
                differences.append(
                    f"table {table!r}: {noun} {item!r} in {expected_label}, "
                    f"missing from {actual_label}"
                )
            for item in sorted(act - exp):
                differences.append(
                    f"table {table!r}: {noun} {item!r} in {actual_label}, "
                    f"not in {expected_label}"
                )

    return differences


def describe_schema_at_path(db_path: Any) -> Dict[str, Dict[str, List[str]]]:
    """Opens a SQLite file read-only and describes its schema."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return describe_schema(conn)
    finally:
        conn.close()
