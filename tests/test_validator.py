"""
Deterministic tests for validator.py. No LLM/network calls -- pure AST checks.

Run from the project root: .venv/bin/pytest tests/test_validator.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

import validator


VALID_QUERIES = [
    "SELECT loan_amount FROM hmda",
    "SELECT * FROM hmda LIMIT 10",
    'SELECT "denial_reason-1", "co-applicant_sex" FROM hmda',
    "SELECT action_taken, COUNT(*) AS n FROM hmda GROUP BY action_taken ORDER BY n DESC",
    "SELECT MEDIAN(loan_amount) FROM hmda WHERE action_taken = 1",
    "select loan_amount from HMDA",  # case-insensitive table name
    "SELECT a.loan_amount FROM hmda a JOIN hmda b ON a.county_code = b.county_code AND a.lei != b.lei",
    # SQL comments and string literals containing forbidden-looking keywords
    # must not influence the result -- only the parsed AST matters.
    "SELECT loan_amount FROM hmda -- DROP TABLE hmda",
    "SELECT 'DROP TABLE' AS note FROM hmda",
    # The denial_reason-1..4 union pattern called out in schema.py.
    """
    SELECT "denial_reason-1" AS reason FROM hmda WHERE action_taken = 3
    UNION ALL
    SELECT "denial_reason-2" AS reason FROM hmda WHERE action_taken = 3
    """,
]


@pytest.mark.parametrize("sql", VALID_QUERIES)
def test_valid_queries_pass(sql):
    assert validator.validate_sql(sql) == []


def test_trailing_semicolon_is_still_one_statement():
    assert validator.validate_sql("SELECT loan_amount FROM hmda;") == []


INVALID_COLUMN_QUERIES = [
    "SELECT fake_column FROM hmda",
    "SELECT loan_amount FROM hmda WHERE not_a_real_column = 1",
    "SELECT loan_amount, credit_score FROM hmda",  # credit score isn't disclosed in HMDA
]


@pytest.mark.parametrize("sql", INVALID_COLUMN_QUERIES)
def test_undocumented_columns_are_rejected(sql):
    violations = validator.validate_sql(sql)
    assert violations
    assert any("column" in v.lower() for v in violations)


MULTI_STATEMENT_QUERIES = [
    "SELECT loan_amount FROM hmda; DROP TABLE hmda;",
    "SELECT loan_amount FROM hmda; SELECT income FROM hmda;",
    "SELECT loan_amount FROM hmda;; SELECT income FROM hmda;",
]


@pytest.mark.parametrize("sql", MULTI_STATEMENT_QUERIES)
def test_multiple_statements_are_rejected(sql):
    violations = validator.validate_sql(sql)
    assert violations
    assert any("one sql statement" in v.lower() for v in violations)


NON_SELECT_STATEMENTS = [
    "INSERT INTO hmda VALUES (1)",
    "UPDATE hmda SET loan_amount = 0",
    "DELETE FROM hmda",
    "DROP TABLE hmda",
    "CREATE TABLE evil AS SELECT * FROM hmda",
    "ALTER TABLE hmda ADD COLUMN x INT",
    "ATTACH 'evil.db' AS e",
    "PRAGMA version",
    "COPY hmda TO 'out.csv'",
]


@pytest.mark.parametrize("sql", NON_SELECT_STATEMENTS)
def test_non_select_statements_are_rejected(sql):
    violations = validator.validate_sql(sql)
    assert violations
    assert any("read-only" in v.lower() for v in violations)


PROHIBITED_TABLE_QUERIES = [
    "SELECT * FROM other_table",
    "SELECT * FROM read_csv('/etc/passwd')",
    "SELECT * FROM 'file.parquet'",
    "SELECT * FROM some_db.hmda",
    "SELECT lei FROM hmda WHERE lei IN (SELECT lei FROM lenders)",
]


@pytest.mark.parametrize("sql", PROHIBITED_TABLE_QUERIES)
def test_prohibited_table_access_is_rejected(sql):
    violations = validator.validate_sql(sql)
    assert violations
    assert any("hmda" in v.lower() and "allowed" in v.lower() for v in violations)


UNSAFE_FUNCTION_QUERIES = [
    "SELECT getenv('PATH')",
    "SELECT current_setting('x') FROM hmda",
    "SELECT version()",
    "SELECT pragma_version()",
    "SELECT setseed(0.5)",
]


@pytest.mark.parametrize("sql", UNSAFE_FUNCTION_QUERIES)
def test_unsafe_functions_are_rejected(sql):
    violations = validator.validate_sql(sql)
    assert violations
    assert any("not allowed" in v.lower() for v in violations)


def test_cte_defining_and_using_an_alias_is_valid():
    sql = """
    WITH capped AS (
        SELECT TRY_CAST(income AS DOUBLE) AS income_num FROM hmda
    )
    SELECT AVG(income_num) FROM capped WHERE income_num > 0
    """
    assert validator.validate_sql(sql) == []


def test_chained_ctes_referencing_each_other_is_valid():
    sql = """
    WITH a AS (SELECT loan_amount FROM hmda),
         b AS (SELECT loan_amount FROM a WHERE loan_amount > 100000)
    SELECT * FROM b
    """
    assert validator.validate_sql(sql) == []


def test_cte_body_referencing_non_hmda_table_is_rejected():
    sql = "WITH leak AS (SELECT * FROM other_table) SELECT * FROM leak"
    violations = validator.validate_sql(sql)
    assert violations
    assert any("other_table" in v for v in violations)


def test_outer_query_mixing_valid_cte_and_bad_table_is_rejected():
    sql = "WITH a AS (SELECT loan_amount FROM hmda) SELECT * FROM a, other_table"
    violations = validator.validate_sql(sql)
    assert violations
    assert any("other_table" in v for v in violations)


def test_empty_query_is_rejected():
    violations = validator.validate_sql("")
    assert violations


def test_unparseable_sql_is_rejected():
    violations = validator.validate_sql("SELECT FROM WHERE ***")
    assert violations
