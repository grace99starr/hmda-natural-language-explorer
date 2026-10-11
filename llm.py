"""
Claude (Anthropic) client used by the NL -> SQL layer.

Reads ANTHROPIC_API_KEY and (optionally) CLAUDE_MODEL from a .env file in the
project root (gitignored, never committed) via python-dotenv. Running this
file directly sends one trivial message to Claude to confirm the API key and
network path work before anything else is built on top of it.
"""

import os
import sys
from typing import Literal

import anthropic
from dotenv import load_dotenv
from pydantic import BaseModel, ValidationError, model_validator

import schema

load_dotenv()

DEFAULT_MODEL = "claude-sonnet-4-6"
MODEL = os.environ.get("CLAUDE_MODEL", DEFAULT_MODEL)

_INSTRUCTIONS = """You are a SQL generator for DuckDB. You translate a natural language
question about the `hmda` table into a single DuckDB SQL query.

Respond with a JSON object with three fields: status, sql, message.

- status: one of "success", "clarification_needed", "unanswerable".
- If the question can be answered, set status to "success", put the SQL query in `sql`,
  and set `message` to null.
  - Query only the `hmda` table, using only the columns documented below.
  - Follow the column notes exactly (e.g. TRY_CAST for text-typed numeric columns, quoting
    hyphenated identifiers, the action_taken approve/deny convention, unioning the
    denial_reason-1..4 columns when relevant).
- If the question references a field that does not exist, asks about data outside this
  table's scope (a year, state, or county other than what's documented), or is otherwise
  unanswerable with this schema, set status to "unanswerable", `sql` to null, and `message`
  to a short reason.
- If the question has multiple materially different interpretations and the schema does
  not resolve them, set status to "clarification_needed", `sql` to null, and `message` to
  a short clarification question.
"""


class SQLGenerationError(Exception):
    """Raised when Claude's response can't be turned into a valid SQLResponse
    (truncated JSON, or valid JSON that fails SQLResponse's own invariants)."""


class SQLResponse(BaseModel):
    status: Literal["success", "clarification_needed", "unanswerable"]
    sql: str | None
    message: str | None

    @model_validator(mode="after")
    def _check_fields_match_status(self) -> "SQLResponse":
        if self.status == "success" and self.sql is None:
            raise ValueError("status is 'success' but sql is null")
        if self.status != "success" and self.sql is not None:
            raise ValueError(f"status is '{self.status}' but sql is set")
        if self.status != "success" and self.message is None:
            raise ValueError(f"status is '{self.status}' but message is null")
        if self.status == "success" and self.message is not None:
            raise ValueError("status is 'success' but message is set")
        return self


# Schema is static across calls, so it's rendered once at import time and marked
# cacheable -- it's ~6K tokens, reused on every question.
_SYSTEM_PROMPT = _INSTRUCTIONS + "\n" + schema.render_schema_for_prompt()


def get_client() -> anthropic.Anthropic:
    """Build an Anthropic client from ANTHROPIC_API_KEY in .env/the environment."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY not found. Create a .env file in the project "
            "root with ANTHROPIC_API_KEY=sk-ant-... (never commit it)."
        )
    return anthropic.Anthropic(api_key=api_key)


def test_connection() -> str:
    """Send a minimal message to Claude and return its reply."""
    client = get_client()
    response = client.messages.create(
        model=MODEL,
        max_tokens=20,
        messages=[{"role": "user", "content": "Reply with exactly: pong"}],
    )
    return response.content[0].text


def generate_sql(question: str) -> SQLResponse:
    """Ask Claude to translate a natural language question into a structured SQL response.

    Raises SQLGenerationError if Claude's response can't be turned into a valid
    SQLResponse (truncated JSON, or JSON that violates SQLResponse's own
    invariants) -- callers should treat this the same as an anthropic.APIError,
    a failure of this specific call, not a reason to crash. A missing API key
    (get_client()'s RuntimeError) is deliberately NOT caught here: that's a
    config problem, not a per-question one, and should fail loudly.
    """
    client = get_client()
    try:
        response = client.messages.parse(
            model=MODEL,
            max_tokens=1024,
            system=[
                {
                    "type": "text",
                    "text": _SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": question}],
            output_format=SQLResponse,
        )
    except ValidationError as e:
        # A response cut short by max_tokens leaves truncated JSON, which fails
        # validation with a json_invalid error rather than a clean field error.
        if any(err["type"] == "json_invalid" for err in e.errors()):
            raise SQLGenerationError(
                "Claude's response was truncated before it could be parsed as JSON. "
                "Increase max_tokens and retry."
            ) from e
        raise SQLGenerationError(f"Claude's response did not match the expected schema: {e}") from e

    if response.stop_reason == "refusal":
        return SQLResponse(status="unanswerable", sql=None, message="Claude declined to answer this question.")
    return response.parsed_output


_SUMMARY_INSTRUCTIONS = """You are summarizing the result of a SQL query run against the `hmda`
mortgage data table, for someone who asked a natural language question.

Write a short, plain-English answer (1-3 sentences) to the question, using ONLY the
query results given below -- do not use outside knowledge or assume anything not
present in the data.

- Use proper units and consistent formatting:
  - Dollar amounts: prefix with "$" and use comma thousands separators, rounded to the
    nearest whole dollar (e.g. "$425,000", not "425000" or "$425,000.00").
  - Percentages: convert ratios to a "%"-suffixed value with at most one decimal place
    (e.g. "48.3%", not "0.483" or "48.342%").
  - Large counts: use comma thousands separators (e.g. "12,458 applications", not "12458").
- If the results are empty or missing (no rows), say so plainly instead of guessing
  an answer -- e.g. "No matching applications were found."
- If the results were truncated (noted below), mention that the answer is based on a
  partial result set.
- Do not repeat the SQL back verbatim; describe the answer in words.
"""


def summarize_result(
    question: str, sql: str, columns: list[str], rows: list[tuple], truncated: bool
) -> str:
    """Ask Claude for a short plain-English answer to `question`, given the rows
    returned by running `sql`. Raises anthropic.APIError on API failure --
    callers that already have the sql/rows in hand should catch this and
    degrade gracefully rather than losing those results.
    """
    client = get_client()
    results_block = (
        f"Columns: {columns}\nRows: {rows}\nTruncated: {truncated}"
        if rows
        else "Rows: (none)"
    )
    user_message = f"Question: {question}\n\nSQL: {sql}\n\n{results_block}"
    response = client.messages.create(
        model=MODEL,
        max_tokens=256,
        system=_SUMMARY_INSTRUCTIONS,
        messages=[{"role": "user", "content": user_message}],
    )
    if response.stop_reason == "refusal":
        return "Claude declined to summarize this result."
    return response.content[0].text


if __name__ == "__main__":
    try:
        reply = test_connection()
    except Exception as e:
        print(f"Connection test failed: {e}")
        sys.exit(1)
    print(f"Connection OK. Claude replied: {reply!r}")
