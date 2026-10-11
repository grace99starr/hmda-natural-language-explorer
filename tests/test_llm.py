"""
Tests for llm.py's generate_sql exception handling.

No real API calls -- client.messages.parse / get_client are monkeypatched.
Covers the full failure surface generate_sql is responsible for normalizing:
truncated JSON and schema-invariant violations both become SQLGenerationError,
while a missing API key (a config problem, not a per-question one) is left to
propagate as a plain RuntimeError.

Run from the project root: .venv/bin/pytest tests/test_llm.py
"""

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from pydantic import ValidationError

import llm


def _fake_client(parse_side_effect):
    def parse(**kwargs):
        raise parse_side_effect

    return SimpleNamespace(messages=SimpleNamespace(parse=parse))


def _validation_error(invalid_json: bool) -> ValidationError:
    """Build a real pydantic ValidationError of the kind generate_sql must handle."""
    if invalid_json:
        # Malformed JSON triggers a "json_invalid" error -- same shape as a
        # response cut short by max_tokens.
        try:
            llm.SQLResponse.model_validate_json("{not valid json")
        except ValidationError as e:
            return e
    else:
        # Well-formed JSON that violates the cross-field invariant (status
        # "success" requires a non-null sql) triggers a "value_error".
        try:
            llm.SQLResponse.model_validate({"status": "success", "sql": None, "message": None})
        except ValidationError as e:
            return e
    raise AssertionError("expected a ValidationError")


def test_truncated_json_becomes_sql_generation_error(monkeypatch):
    monkeypatch.setattr(llm, "get_client", lambda: _fake_client(_validation_error(invalid_json=True)))

    with pytest.raises(llm.SQLGenerationError, match="truncated"):
        llm.generate_sql("What is the average loan amount?")


def test_schema_invariant_violation_becomes_sql_generation_error(monkeypatch):
    monkeypatch.setattr(llm, "get_client", lambda: _fake_client(_validation_error(invalid_json=False)))

    with pytest.raises(llm.SQLGenerationError, match="did not match the expected schema"):
        llm.generate_sql("What is the average loan amount?")


def test_missing_api_key_is_not_wrapped(monkeypatch):
    def boom():
        raise RuntimeError("ANTHROPIC_API_KEY not found.")

    monkeypatch.setattr(llm, "get_client", boom)

    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        llm.generate_sql("What is the average loan amount?")
