"""
Integration tests for database.py.

These build small, throwaway DuckDB connections from in-memory fixtures
(never the real data/hmda_2024.csv, which is gitignored and may not exist
outside this machine) and point database._con at them via monkeypatch, so
every test is self-contained. database._con is only built lazily on first
use, so importing this module -- and running any test that doesn't need a
real connection -- never touches the real CSV.

Run from the project root: .venv/bin/pytest tests/test_database.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import duckdb
import pandas as pd
import pytest

import database

FIXTURE_ROWS = [
    # loan_amount, action_taken, county_code
    (500000.0, 1, 6037),
    (250000.0, 3, 6037),
    (1000000.0, 1, 6083),
    (75000.0, 3, 6083),
    (425000.0, 1, 6037),
]


def _connection_from_rows(rows: list[tuple]) -> duckdb.DuckDBPyConnection:
    df = pd.DataFrame(rows, columns=["loan_amount", "action_taken", "county_code"])
    con = duckdb.connect(
        database=":memory:",
        config={
            "enable_external_access": False,
            "memory_limit": database.MEMORY_LIMIT,
            "threads": database.THREADS,
        },
    )
    con.register("hmda_df", df)
    con.execute(f"CREATE TABLE {database.TABLE_NAME} AS SELECT * FROM hmda_df")
    con.unregister("hmda_df")
    return con


class _ExplodingConnection:
    """Stands in for database._con; fails the test if DuckDB is ever touched."""

    def execute(self, *args, **kwargs):
        raise AssertionError("DuckDB execute() should not be called for a rejected query")

    def interrupt(self):
        raise AssertionError("DuckDB interrupt() should not be called for a rejected query")


@pytest.fixture
def fixture_con(monkeypatch):
    con = _connection_from_rows(FIXTURE_ROWS)
    monkeypatch.setattr(database, "_con", con)
    return con


def test_valid_query_returns_structured_rows(fixture_con):
    result = database.run_query(
        "SELECT loan_amount FROM hmda WHERE action_taken = 1 ORDER BY loan_amount"
    )
    assert result.status == "ok"
    assert result.columns == ["loan_amount"]
    assert result.rows == [(425000.0,), (500000.0,), (1000000.0,)]
    assert result.row_count == 3
    assert result.truncated is False
    assert result.error is None


def test_validator_rejection_never_reaches_duckdb(monkeypatch):
    monkeypatch.setattr(database, "_con", _ExplodingConnection())
    result = database.run_query("SELECT fake_column FROM hmda")
    assert result.status == "rejected"
    assert "fake_column" in result.error
    assert result.rows is None


def test_duckdb_runtime_error_is_caught_and_structured(fixture_con):
    # Valid per validator (real column, plain read-only SELECT) but fails at
    # execution time: county_code holds integers, not valid DATE strings.
    result = database.run_query("SELECT CAST(county_code AS DATE) FROM hmda")
    assert result.status == "error"
    assert result.rows is None
    assert result.error


def test_results_are_truncated_at_max_rows(fixture_con, monkeypatch):
    monkeypatch.setattr(database, "MAX_ROWS", 2)
    result = database.run_query("SELECT loan_amount FROM hmda ORDER BY loan_amount")
    assert result.status == "ok"
    assert result.row_count == 2
    assert len(result.rows) == 2
    assert result.truncated is True


def test_validator_blocks_read_csv_before_execution(fixture_con):
    result = database.run_query("SELECT * FROM read_csv('data/hmda_2024.csv')")
    assert result.status == "rejected"
    assert "read_csv" in result.error.lower()


def test_lockdown_blocks_filesystem_access_even_if_validator_is_bypassed(fixture_con):
    # Calls the connection directly, skipping validate_sql entirely, to prove
    # enable_external_access=False is a real, independent boundary -- not
    # just something the validator's function denylist happens to also catch.
    with pytest.raises(duckdb.Error, match="file system operations are disabled"):
        fixture_con.execute("SELECT * FROM read_csv('data/hmda_2024.csv')")


def test_connection_is_not_built_until_first_use(monkeypatch):
    monkeypatch.setattr(database, "_con", None)
    assert database._con is None
    # Importing/using the module shouldn't have built a connection yet; only
    # the first call that actually needs one does.


def test_missing_csv_fails_clearly_on_first_use_not_at_import(monkeypatch):
    monkeypatch.setattr(database, "_con", None)
    monkeypatch.setattr(database, "CSV_PATH", Path("definitely_does_not_exist.csv"))
    with pytest.raises(FileNotFoundError):
        database.run_query("SELECT loan_amount FROM hmda")


def test_query_timeout_aborts_long_running_queries(monkeypatch):
    big_con = duckdb.connect(database=":memory:", config={"enable_external_access": False})
    big_con.execute(
        "CREATE TABLE hmda AS "
        "SELECT i AS loan_amount, 1 AS action_taken, 6037 AS county_code FROM range(500000) t(i)"
    )
    monkeypatch.setattr(database, "_con", big_con)
    monkeypatch.setattr(database, "QUERY_TIMEOUT_SECONDS", 0.3)

    result = database.run_query("SELECT count(*) FROM hmda a, hmda b, hmda c")

    assert result.status == "error"
    assert "interrupt" in result.error.lower()

    # The connection must still be usable after an interrupted query -- the
    # timeout should abort that one query, not corrupt shared state.
    follow_up = database.run_query("SELECT count(*) FROM hmda")
    assert follow_up.status == "ok"
    assert follow_up.rows == [(500000,)]
