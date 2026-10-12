"""
Runs evaluation/cases.json through the real NL->SQL pipeline (core.answer_question()),
writing a full record -- including the generated SQL/results side by side with the
reference SQL/results -- to evaluation/results.json, and printing a status-match
accuracy summary by category.

Scoring is status-match only (did the pipeline return the expected status?). Whether
the generated SQL's results agree with the reference query's is left for a human to
judge by reading the two result sets side by side in results.json: the same
information can come back in a different shape (extra columns, different grouping,
a differently-defined rate, different rounding) without either query being wrong.

Run from the project root: .venv/bin/python evaluation/evaluate.py
Requires ANTHROPIC_API_KEY in .env -- this makes real Claude API calls for every case.

Reference SQL is developer-authored ground truth, not LLM output, so it is run
directly against the shared DuckDB connection, bypassing validator.validate_sql()
(that validator exists to sandbox untrusted LLM-generated SQL; some reference
queries, e.g. the UNPIVOT-based one, use column names the validator wouldn't
recognize and would otherwise reject).
"""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import duckdb

import core
import database

CASES_PATH = Path(__file__).parent / "cases.json"
REFERENCE_SQL_PATH = Path(__file__).parent / "reference_queries.sql"
RESULTS_PATH = Path(__file__).parent / "results.json"

CATEGORIES = ("basic", "complex", "unanswerable", "ambiguous")

REFERENCE_BLOCK_RE = re.compile(r"--\s*id:\s*(\d+)\s*\n(.*?)(?=--\s*id:\s*\d+|\Z)", re.DOTALL)


def load_cases() -> list[dict]:
    return json.loads(CASES_PATH.read_text())


def load_reference_queries() -> dict[int, str]:
    """Parse reference_queries.sql into {id: sql} by splitting on "-- id: N" markers."""
    text = REFERENCE_SQL_PATH.read_text()
    return {int(match.group(1)): match.group(2).strip() for match in REFERENCE_BLOCK_RE.finditer(text)}


def run_reference_query(con: duckdb.DuckDBPyConnection, sql: str) -> tuple[list[str] | None, list[tuple] | None, str | None]:
    """Execute sql directly against con. Returns (columns, rows, error)."""
    try:
        cursor = con.cursor()
        cursor.execute(sql)
        columns = [d[0] for d in cursor.description]
        rows = cursor.fetchall()
        return columns, rows, None
    except duckdb.Error as e:
        return None, None, str(e)


def evaluate_case(case: dict, reference_sql: dict[int, str], con: duckdb.DuckDBPyConnection) -> dict:
    result = core.answer_question(case["question"])

    reference_sql_text = reference_columns = reference_rows = reference_error = None
    reference_sql_id = case["reference_sql_id"]
    if reference_sql_id is not None:
        reference_sql_text = reference_sql[reference_sql_id]
        reference_columns, reference_rows, reference_error = run_reference_query(con, reference_sql_text)

    return {
        "id": case["id"],
        "category": case["category"],
        "question": case["question"],
        "expected_status": case["expected_status"],
        "actual_status": result.status,
        "status_match": result.status == case["expected_status"],
        "generated_sql": result.sql,
        "answer": result.answer,
        "pipeline_error": result.error,
        "pipeline_columns": result.columns,
        "pipeline_rows": result.rows,
        "reference_sql": reference_sql_text,
        "reference_columns": reference_columns,
        "reference_rows": reference_rows,
        "reference_error": reference_error,
    }


def summarize(results: list[dict]) -> dict:
    def ratio(n: int, d: int) -> str:
        return f"{n}/{d} ({round(100 * n / d) if d else 0}%)"

    summary = {
        category: {"status_match": ratio(sum(r["status_match"] for r in results if r["category"] == category),
                                          sum(1 for r in results if r["category"] == category))}
        for category in CATEGORIES
    }
    summary["total"] = {"status_match": ratio(sum(r["status_match"] for r in results), len(results))}
    return summary


def print_summary(summary: dict) -> None:
    print(f"{'Category':<15}{'Status accuracy':<20}")
    for category in CATEGORIES:
        print(f"{category:<15}{summary[category]['status_match']:<20}")
    print(f"{'TOTAL':<15}{summary['total']['status_match']:<20}")


def main() -> None:
    cases = load_cases()
    reference_sql = load_reference_queries()
    con = database._get_connection()

    results = [evaluate_case(case, reference_sql, con) for case in cases]

    RESULTS_PATH.write_text(json.dumps({"cases": results, "summary": summarize(results)}, indent=2))
    print_summary(summarize(results))
    print(f"\nFull results written to {RESULTS_PATH}")


if __name__ == "__main__":
    main()
