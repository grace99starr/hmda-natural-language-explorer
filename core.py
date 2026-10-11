"""
End-to-end pipeline: natural language question -> SQL -> results -> plain-English answer.

Wires together llm.py (NL -> SQL, and results -> plain English), database.py
(validates and executes the SQL), and nothing else -- validation itself is
not duplicated here, it already happens inside database.run_query().
"""

import anthropic
from dataclasses import dataclass
from typing import Literal

import database
import llm

Status = Literal["success", "clarification_needed", "unanswerable", "rejected", "error"]


@dataclass
class PipelineResult:
    question: str
    status: Status
    sql: str | None
    columns: list[str] | None
    rows: list[tuple] | None
    row_count: int | None
    truncated: bool
    answer: str | None
    error: str | None


def answer_question(question: str) -> PipelineResult:
    """Run the full pipeline for one natural language `question`."""
    try:
        sql_response = llm.generate_sql(question)
    except (anthropic.APIError, llm.SQLGenerationError) as e:
        return PipelineResult(
            question=question,
            status="error",
            sql=None,
            columns=None,
            rows=None,
            row_count=None,
            truncated=False,
            answer=None,
            error=f"Generating SQL failed: {e}",
        )

    if sql_response.status != "success":
        # clarification_needed or unanswerable: nothing to execute or summarize.
        return PipelineResult(
            question=question,
            status=sql_response.status,
            sql=None,
            columns=None,
            rows=None,
            row_count=None,
            truncated=False,
            answer=sql_response.message,
            error=None,
        )

    sql = sql_response.sql
    query_result = database.run_query(sql)

    if query_result.status != "ok":
        # "rejected" (failed validator.validate_sql) or "error" (DuckDB runtime error).
        return PipelineResult(
            question=question,
            status=query_result.status,
            sql=sql,
            columns=None,
            rows=None,
            row_count=None,
            truncated=False,
            answer=None,
            error=query_result.error,
        )

    try:
        answer = llm.summarize_result(
            question, sql, query_result.columns, query_result.rows, query_result.truncated
        )
        error = None
    except anthropic.APIError as e:
        # The query itself succeeded -- don't lose the sql/rows just because
        # the follow-up summarization call failed.
        answer = None
        error = f"Query succeeded, but summarizing the answer failed: {e}"

    return PipelineResult(
        question=question,
        status="success",
        sql=sql,
        columns=query_result.columns,
        rows=query_result.rows,
        row_count=query_result.row_count,
        truncated=query_result.truncated,
        answer=answer,
        error=error,
    )
