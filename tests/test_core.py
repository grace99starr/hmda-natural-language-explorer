"""
Tests for core.py's end-to-end pipeline.

llm.generate_sql, llm.summarize_result, and database.run_query are monkeypatched
with stand-ins (SimpleNamespace / lambdas) -- no real API or DuckDB calls, and
no network access. Covers the happy path plus every short-circuit branch,
including the three bugs a prior review found: an unguarded API error on the
first LLM call, a refusal that used to raise IndexError, and a RuntimeError
(truncated response) escaping uncaught.

Run from the project root: .venv/bin/pytest tests/test_core.py
"""

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent))

import anthropic
import pytest

import core
import database
import llm


def _sql_response(status, sql=None, message=None):
    return SimpleNamespace(status=status, sql=sql, message=message)


def _query_result(status, columns=None, rows=None, row_count=None, truncated=False, error=None):
    return SimpleNamespace(
        status=status, columns=columns, rows=rows, row_count=row_count,
        truncated=truncated, error=error,
    )


def test_clarification_needed_short_circuits_before_db_and_summary(monkeypatch):
    monkeypatch.setattr(llm, "generate_sql", lambda q: _sql_response("clarification_needed", message="Which years?"))
    monkeypatch.setattr(database, "run_query", lambda sql: pytest.fail("should not execute SQL"))

    result = core.answer_question("What's the approval rate?")

    assert result.status == "clarification_needed"
    assert result.sql is None
    assert result.answer == "Which years?"
    assert result.error is None


def test_unanswerable_short_circuits_before_db_and_summary(monkeypatch):
    monkeypatch.setattr(llm, "generate_sql", lambda q: _sql_response("unanswerable", message="Out of scope."))
    monkeypatch.setattr(database, "run_query", lambda sql: pytest.fail("should not execute SQL"))

    result = core.answer_question("What about Texas?")

    assert result.status == "unanswerable"
    assert result.sql is None
    assert result.answer == "Out of scope."


def test_db_rejected_sql_is_preserved_with_no_answer(monkeypatch):
    monkeypatch.setattr(llm, "generate_sql", lambda q: _sql_response("success", sql="SELECT fake_column FROM hmda"))
    monkeypatch.setattr(database, "run_query", lambda sql: _query_result("rejected", error="Unknown column 'fake_column'"))

    result = core.answer_question("q")

    assert result.status == "rejected"
    assert result.sql == "SELECT fake_column FROM hmda"
    assert result.error == "Unknown column 'fake_column'"
    assert result.answer is None


def test_db_runtime_error_is_preserved_with_no_answer(monkeypatch):
    monkeypatch.setattr(llm, "generate_sql", lambda q: _sql_response("success", sql="SELECT CAST(x AS DATE) FROM hmda"))
    monkeypatch.setattr(database, "run_query", lambda sql: _query_result("error", error="Conversion error"))

    result = core.answer_question("q")

    assert result.status == "error"
    assert result.sql == "SELECT CAST(x AS DATE) FROM hmda"
    assert result.error == "Conversion error"
    assert result.answer is None


def test_success_path_returns_sql_rows_and_answer(monkeypatch):
    monkeypatch.setattr(llm, "generate_sql", lambda q: _sql_response("success", sql="SELECT loan_amount FROM hmda"))
    monkeypatch.setattr(
        database, "run_query",
        lambda sql: _query_result("ok", columns=["loan_amount"], rows=[(425000.0,)], row_count=1, truncated=False),
    )
    monkeypatch.setattr(llm, "summarize_result", lambda *a, **k: "The loan amount is $425,000.")

    result = core.answer_question("What is the loan amount?")

    assert result.status == "success"
    assert result.sql == "SELECT loan_amount FROM hmda"
    assert result.rows == [(425000.0,)]
    assert result.answer == "The loan amount is $425,000."
    assert result.error is None


def test_summarize_api_error_preserves_sql_and_rows(monkeypatch):
    """Bug fix: a failure in the second LLM call must not lose the query results."""
    monkeypatch.setattr(llm, "generate_sql", lambda q: _sql_response("success", sql="SELECT loan_amount FROM hmda"))
    monkeypatch.setattr(
        database, "run_query",
        lambda sql: _query_result("ok", columns=["loan_amount"], rows=[(425000.0,)], row_count=1, truncated=False),
    )

    def boom(*a, **k):
        raise anthropic.APIConnectionError(request=None)

    monkeypatch.setattr(llm, "summarize_result", boom)

    result = core.answer_question("What is the loan amount?")

    assert result.status == "success"
    assert result.sql == "SELECT loan_amount FROM hmda"
    assert result.rows == [(425000.0,)]
    assert result.answer is None
    assert "summarizing the answer failed" in result.error


def test_generate_sql_api_error_does_not_crash(monkeypatch):
    """Bug fix: a transient API error on the first LLM call must be caught, not raised."""
    def boom(q):
        raise anthropic.APIConnectionError(request=None)

    monkeypatch.setattr(llm, "generate_sql", boom)

    result = core.answer_question("What is the average loan amount?")

    assert result.status == "error"
    assert result.sql is None
    assert "Generating SQL failed" in result.error


def test_generate_sql_truncated_response_does_not_crash(monkeypatch):
    """Bug fix: llm.generate_sql's SQLGenerationError on a truncated response must be caught too."""
    def boom(q):
        raise llm.SQLGenerationError("Claude's response was truncated before it could be parsed as JSON.")

    monkeypatch.setattr(llm, "generate_sql", boom)

    result = core.answer_question("What is the average loan amount?")

    assert result.status == "error"
    assert result.sql is None
    assert "Generating SQL failed" in result.error


def test_generate_sql_schema_invariant_violation_does_not_crash(monkeypatch):
    """Bug fix: valid JSON that fails SQLResponse's cross-field invariant must be
    caught too, not just outright-truncated JSON."""
    def boom(q):
        raise llm.SQLGenerationError("Claude's response did not match the expected schema: ...")

    monkeypatch.setattr(llm, "generate_sql", boom)

    result = core.answer_question("What is the average loan amount?")

    assert result.status == "error"
    assert result.sql is None
    assert "Generating SQL failed" in result.error


def test_missing_api_key_propagates_uncaught(monkeypatch):
    """A missing API key is a config problem, not a per-question failure --
    it must NOT be swallowed into a PipelineResult."""
    def boom(q):
        raise RuntimeError("ANTHROPIC_API_KEY not found.")

    monkeypatch.setattr(llm, "generate_sql", boom)

    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        core.answer_question("What is the average loan amount?")


def test_summarize_refusal_returns_fallback_text_not_indexerror(monkeypatch):
    """Bug fix: a safety refusal (empty content) must not raise IndexError."""
    fake_response = SimpleNamespace(stop_reason="refusal", content=[])
    fake_client = SimpleNamespace(messages=SimpleNamespace(create=lambda **k: fake_response))
    monkeypatch.setattr(llm, "get_client", lambda: fake_client)

    answer = llm.summarize_result("q", "SELECT 1", ["x"], [(1,)], False)

    assert answer == "Claude declined to summarize this result."
