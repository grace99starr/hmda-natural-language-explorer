"""
DuckDB execution layer for validated NL -> SQL queries.

Loads data/hmda_2024.csv into an in-memory `hmda` table on first use, on a
connection configured so DuckDB cannot touch the filesystem or network while
serving queries (enable_external_access=False). Every query must also pass
validator.validate_sql() before it reaches DuckDB -- the lockdown config is a
second, independent boundary in case something gets past the validator's
function denylist (which itself admits it's "not exhaustive").

Kept intentionally simple: one process-wide connection, synchronous
execution, no connection pooling. A single lock serializes every call to
run_query(), so only one query runs at a time -- this also means only one
thread can ever be the one building the connection on first use, with no
separate init-time locking to reason about. Each call gets its own cursor
(a lightweight handle onto the same connection, not a new connection) so its
watchdog timer's con.interrupt() can only ever cancel that call's own query,
never bleed into whatever runs after it.
"""

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import duckdb
import pandas as pd

import validator

CSV_PATH = Path(__file__).parent / "data" / "hmda_2024.csv"
TABLE_NAME = "hmda"

MAX_ROWS = 10_000
QUERY_TIMEOUT_SECONDS = 10
MEMORY_LIMIT = "2GB"
THREADS = 2

_con: duckdb.DuckDBPyConnection | None = None
_lock = threading.Lock()


@dataclass
class QueryResult:
    status: Literal["ok", "rejected", "error"]
    columns: list[str] | None
    rows: list[tuple] | None
    row_count: int | None
    truncated: bool
    error: str | None


def _build_connection(csv_path: Path) -> duckdb.DuckDBPyConnection:
    """Create the locked-down connection and load `hmda` from csv_path.

    The CSV is read with pandas, not DuckDB's own read_csv, so the
    connection can already have enable_external_access=False in place --
    DuckDB itself never touches the filesystem.
    """
    con = duckdb.connect(
        database=":memory:",
        config={
            "enable_external_access": False,
            "memory_limit": MEMORY_LIMIT,
            "threads": THREADS,
        },
    )
    df = pd.read_csv(csv_path, low_memory=False)
    con.register("hmda_df", df)
    con.execute(f"CREATE TABLE {TABLE_NAME} AS SELECT * FROM hmda_df")
    con.unregister("hmda_df")
    return con


def _get_connection() -> duckdb.DuckDBPyConnection:
    """Return the shared connection, building it on the first call.

    Only called from within run_query() while _lock is held, so there's no
    separate race to guard against here -- the query lock already ensures
    at most one caller can be building (or using) the connection at a time.
    """
    global _con
    if _con is None:
        _con = _build_connection(CSV_PATH)
    return _con


def run_query(sql: str) -> QueryResult:
    """Validate and execute `sql`, returning a structured, UI-ready result.

    Any violation from validator.validate_sql() short-circuits to a
    "rejected" result without DuckDB ever seeing the query. Results are
    capped at MAX_ROWS (QueryResult.truncated reports whether more rows
    existed) and execution is aborted after QUERY_TIMEOUT_SECONDS. Only one
    call runs at a time process-wide; concurrent callers simply wait their
    turn for the shared connection.
    """
    violations = validator.validate_sql(sql)
    if violations:
        return QueryResult(
            status="rejected",
            columns=None,
            rows=None,
            row_count=None,
            truncated=False,
            error="; ".join(violations),
        )

    with _lock:
        cursor = _get_connection().cursor()
        timer = threading.Timer(QUERY_TIMEOUT_SECONDS, cursor.interrupt)
        timer.start()
        try:
            cursor.execute(sql)
            columns = [d[0] for d in cursor.description]
            rows = cursor.fetchmany(MAX_ROWS)
            truncated = cursor.fetchone() is not None
        except duckdb.Error as e:
            return QueryResult(
                status="error",
                columns=None,
                rows=None,
                row_count=None,
                truncated=False,
                error=str(e),
            )
        finally:
            timer.cancel()

        return QueryResult(
            status="ok",
            columns=columns,
            rows=rows,
            row_count=len(rows),
            truncated=truncated,
            error=None,
        )
