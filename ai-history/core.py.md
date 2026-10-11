# core.py

**Date:** October 11, 2026  
**Messages:** 48

---

## User

explore the codebase and its architecture. do not change anything

---

## Assistant

[Tool: Bash]

---

## Assistant

Small, focused codebase. Let me read the core modules and README in parallel.

---

## Assistant

[Tool: Bash]

---

## Assistant

## Architecture Overview

This is a natural-language-to-SQL query explorer for 2024 HMDA (mortgage disclosure) data, scoped to LA and Santa Barbara counties, CA. It's a small, well-tested pipeline with four core modules plus tests — no app/CLI entrypoint yet, no web framework.

**Pipeline: NL question → validated SQL → DuckDB results**

1. **[schema.py](schema.py)** — Single source of truth for the data dictionary: a `Column` dataclass list describing all 99 CSV columns (name, SQL type, category, description, coded values, caveats like "stored as VARCHAR, TRY_CAST before aggregating"). Exposes `render_schema_for_prompt()` (markdown dump spliced into the LLM system prompt) and `validate_schema()` (checks the dataclass list against the live DuckDB table to catch drift). Also richly encodes domain knowledge — e.g. loan_amount skew, denial reasons spanning 4 columns, action_taken semantics for "approved"/"denied".

2. **[llm.py](llm.py)** — Wraps the Anthropic client (`claude-sonnet-4-6` by default, overridable via `CLAUDE_MODEL` env var). `generate_sql(question)` sends the question plus the full rendered schema (cached via `cache_control: ephemeral`) and parses a structured `SQLResponse` (status: `success` / `clarification_needed` / `unanswerable`, plus `sql` and `message`) using Pydantic + `client.messages.parse`. A `model_validator` enforces field consistency per status. Handles truncated/refused responses explicitly.

3. **[validator.py](validator.py)** — Independent static AST gate (via `sqlglot`, DuckDB dialect) run on any LLM-generated SQL *before* execution. Three checks: exactly one statement, read-only (SELECT/UNION/INTERSECT/EXCEPT only), and only the `hmda` table + documented columns (from `schema.py`) + CTE aliases are referenced. Also denylists functions that could leak filesystem/env/session info (`read_csv`, `getenv`, `pragma_*`, etc.) as defense-in-depth.

4. **[database.py](database.py)** — Execution layer. Builds one process-wide, locked-down in-memory DuckDB connection (`enable_external_access=False`, memory/thread limits) on first use, loading the CSV via pandas (so DuckDB itself never touches disk). `run_query()` re-validates via `validator.validate_sql()` (defense in depth), then executes under a global lock with a per-cursor watchdog timer (`QUERY_TIMEOUT_SECONDS=10`) and row cap (`MAX_ROWS=10_000`), returning a structured `QueryResult` (`ok`/`rejected`/`error`).

**Defense in depth, consistently applied:** every layer assumes the layer below it might be bypassed — validator checks are redundant with DB lockdown config, and schema checks are redundant with runtime DuckDB errors.

**Tests** ([tests/](tests/)) mirror this: `test_validator.py` is pure AST unit tests (no network/DB), `test_database.py` uses tiny in-memory fixtures via monkeypatching `database._con` (never touches the real gitignored CSV), and `eval_sample_questions.py` is a manual/non-pytest script that exercises the real LLM against sample questions grouped by expected status.

**Data flow is one-directional and not yet wired end-to-end**: `llm.py` produces SQL text but nothing currently pipes `generate_sql()`'s output into `database.run_query()` — that integration (plus any UI/CLI) appears to be the next unbuilt piece. `data/hmda_2024.csv` (99 columns, ~209K lines) is gitignored; `requirements.txt` pins anthropic, duckdb, pandas, pydantic, sqlglot, pytest, python-dotenv, jupyter.

No changes were made — this was exploration only.

---

## User

i want to implement core.py. propose a simple plan for connecting all the modules into an e2e pipeline. the pipeline should accept a natural language question, generate sql with llm.py, handle clarification and uanswerable questions without trying to execute sql, execute valid sql with database.run_query(), return a structured result with the sql query, sql results, and any errors, give a simple plain english answer based on the query results. keep it simple and understandable. no need to duplicate validation or anything that is already handled elsewhere. just give the plan, do not implement yet

---

## Assistant

## Proposed plan for `core.py`

**One public function**, something like:

```python
def answer_question(question: str) -> PipelineResult
```

### New result type (lives in core.py)

```python
@dataclass
class PipelineResult:
    question: str
    status: Literal["success", "clarification_needed", "unanswerable", "rejected", "error"]
    sql: str | None
    columns: list[str] | None
    rows: list[tuple] | None
    row_count: int | None
    truncated: bool
    answer: str | None      # plain-English answer, or the clarification/error message
    error: str | None
```

Statuses `"rejected"` and `"error"` extend `llm.SQLResponse`'s three — they come from `database.QueryResult` when SQL passes the LLM but fails validation or execution. One flat enum keeps the UI logic simple (switch on `status`, don't nest).

### Flow

1. **Generate SQL** — call `llm.generate_sql(question)`.
2. **Short-circuit on non-success** — if status is `clarification_needed` or `unanswerable`, return a `PipelineResult` immediately with that status, `sql=None`, no columns/rows, `answer=None`, and `error=None` (the `message` from `SQLResponse` *is* the thing to show the user — pass it through, maybe reuse `answer` field for it rather than adding a separate `message` field, to avoid two near-duplicate fields). **No DB call, no validation duplicated.**
3. **Execute SQL** — call `database.run_query(sql)` (this already re-validates internally; core.py does not re-check anything).
4. **Short-circuit on rejected/error** — if `QueryResult.status` is `"rejected"` or `"error"`, return a `PipelineResult` with that status, the `sql` that was attempted, `error=result.error`, `answer=None`. **No answer generation for failed queries** — nothing to summarize.
5. **Generate plain-English answer** — only reached on `QueryResult.status == "ok"`. Call a new, small function `llm.summarize_result(question, sql, columns, rows, truncated)` that sends the question + SQL + result rows to Claude and asks for a short plain-English answer (plain text response, not structured — no need for the `SQLResponse` schema here). Mention `truncated` in the prompt so Claude can caveat the answer if results were capped at `MAX_ROWS`.
6. **Return** `PipelineResult(status="success", sql=sql, columns=..., rows=..., row_count=..., truncated=..., answer=<summary>, error=None)`.

### What's new vs. reused

- **Reused as-is**: `llm.generate_sql`, `validator` (indirectly, via `database.run_query`), `database.run_query`.
- **One new addition**: a second, simple LLM call — `llm.summarize_result(...)` — added to `llm.py` (not core.py), since it's still "talking to Claude" and keeps core.py as pure orchestration with no prompt strings of its own. This can be a plain-text completion (no `output_format=`), since there's no need for structured status/sql/message here — just a string answer.

### Things deliberately left simple / out of scope

- No retries on validation/SQL errors (e.g. asking the LLM to fix its own SQL) — just surface the error.
- No caching of repeated questions.
- No streaming — synchronous end-to-end call.
- No CLI/UI wiring yet — just the one function other code can call.

Let me know if you want the `message`/`answer` field merged as I suggested, or kept separate — that's the main naming decision before I implement.

---

## User

proceed with implementation with a few additional things: make sure the prompt for the second llm call to summarize results tells Claude to use only the query results, proper units, and note empty or missing results. handle anthropic api errors without losing the sql and query results if only the summarization fails. keep this simple and explainable

---

## Assistant

[Tool: Bash]

---

## Assistant

[Tool: Bash]

---

## Assistant

[Tool: Bash]

---

## Assistant

Good — `anthropic.APIError` is the common base for all API-related errors (connection, status, etc.). Now let me add `summarize_result` to `llm.py` and write `core.py`.

---

## Assistant

Now let's write `core.py`:

---

## Assistant

[Tool: Bash]

---

## Assistant

[Tool: Bash]

---

## Assistant

All five branches work as expected. Let's make sure existing tests still pass.

---

## Assistant

All 51 existing tests still pass, and I manually verified all five code paths of the new pipeline.

## Summary

**[llm.py](llm.py)** — added `summarize_result(question, sql, columns, rows, truncated)`: a plain-text (non-structured) Claude call with a system prompt instructing it to answer using only the given rows, use proper units, and flag empty/missing or truncated results. It raises `anthropic.APIError` on failure rather than swallowing it, so the caller decides what to do.

**[core.py](core.py)** — new `answer_question(question)` orchestrates the pipeline:
1. `llm.generate_sql` → if not `"success"` (i.e. `clarification_needed`/`unanswerable`), return immediately with no SQL/DB call, using `message` as the `answer`.
2. `database.run_query(sql)` → if not `"ok"` (`"rejected"`/`"error"`), return with `sql` preserved but no `answer` (nothing to summarize).
3. On `"ok"`, call `llm.summarize_result(...)` inside a `try`/`except anthropic.APIError` — if summarization fails, the result still carries `status="success"`, the full `sql`/`columns`/`rows`, with `answer=None` and `error` explaining only the summarization step failed.

`PipelineResult` is a flat dataclass (`question`, `status`, `sql`, `columns`, `rows`, `row_count`, `truncated`, `answer`, `error`) so callers can switch on one `status` field without nested result types.

---

## User

another claude reviewed these changes and called out two bugs. propose a plan to address them. keep it simple and straightforward. do not implement until i approve. Correctness bugs (both confirmed by execution)
1. `generate_sql`’s API failures aren’t handled at all — asymmetric with `summarize_result`.
`answer_question()` wraps the second LLM call (`summarize_result`) in `try/except anthropic.APIError`, with a comment explaining why: “don’t lose the sql/rows just because the follow-up summarization call failed.” That reasoning applies equally to the first LLM call (`generate_sql`) — a rate limit or network blip there is exactly the same failure class — but nothing guards it. I confirmed this by simulating a transient failure:

```
llm.generate_sql = lambda q: (_ for _ in ()).throw(anthropic.APIConnectionError(request=None))
core.answer_question("What is the average loan amount?")
-> raised uncaught out of answer_question(): APIConnectionError: Connection error.
```

`Status` and `PipelineResult` are set up to represent every outcome as a structured value (`success`/`clarification_needed`/`unanswerable`/`rejected`/`error`), but a transient API error on the first call bypasses all of that and crashes the caller directly — undermining the reason `PipelineResult` exists in the first place. Any caller (a Streamlit UI, say) has to wrap `answer_question()` in its own try/except anyway, which defeats the point of the typed result.
2. The `summarize_result` guard itself doesn’t catch what a refusal actually raises.
I checked what `llm.summarize_result()` does on a Claude safety refusal — it has no `stop_reason` check at all, just `response.content[0].text`. On a pre-output refusal `content` is `[]`, so this raises a plain `IndexError`, not an `anthropic.APIError`:

```
# simulated refusal response: content=[], stop_reason="refusal"
llm.summarize_result(...) -> raised: IndexError: list index out of range
is this an anthropic.APIError? False
```

So the one call `core.py` does guard isn’t actually protected against this failure mode — `except anthropic.APIError` lets the `IndexError` straight through, and `answer_question()` crashes with sql/rows already in hand, which is precisely the scenario the comment says it’s trying to avoid. (I verified separately that `anthropic.APIConnectionError`/`APITimeoutError` are subclasses of `APIError` in this SDK version, so the except clause itself is the right type for genuine API errors — it’s specifically the refusal path in `llm.py` that’s the gap.) This is really a bug in `llm.summarize_result`, but it’s only exposed as broken behavior here in `core.py`’s pipeline, so worth flagging in this review.
Overcomplication: none found
The module is appropriately thin — it’s a straight linear pipeline (generate SQL → run it → summarize), with no unnecessary abstraction, no premature generalization, and the one deliberate complexity (preserving `sql`/`rows` when summarization fails) is well-motivated even though its implementation has the bug above. `Status`/`PipelineResult` correctly enumerate exactly the outcomes reachable from the two upstream modules — I traced every branch and all 5 `Status` values are reachable and consistent with what `llm.SQLResponse.status` and `database.QueryResult.status` actually produce.
Minor, not a complexity problem: the three `PipelineResult(...)` construction sites each spell out all 9 fields by hand, mostly repeating `None`/`False` defaults. Not overcomplicated — if anything, slightly under-abstracted — but a small factory (e.g. a “failure” constructor defaulting the unused fields) would remove the repetition and the risk of one call site drifting if a field gets added later.
Worth knowing, not a complexity issue: when `summarize_result` fails, the code returns `status="success"` with `error` set and `answer=None`. That’s a deliberate, defensible choice (the query itself did succeed), but it means `status` alone doesn’t tell a consumer whether to trust `answer` — they also need to check `error is not None` even on `"success"`. Worth a one-line doc note so a future UI author doesn’t miss it.
No test file exists yet for `core.py`, which is how both bugs above went unverified — worth adding one that specifically simulates API failures on both LLM calls, not just the happy path.

---

## Assistant

## Plan

### Bug 1 — unguarded `generate_sql` call in `core.py`

Wrap the first LLM call the same way the second one already is:

```python
try:
    sql_response = llm.generate_sql(question)
except anthropic.APIError as e:
    return PipelineResult(
        question=question, status="error", sql=None, columns=None,
        rows=None, row_count=None, truncated=False, answer=None,
        error=f"Generating SQL failed: {e}",
    )
```

Reuses the existing `"error"` status (already a valid `Status` value) — no new states needed. Symmetric with the `summarize_result` guard already in place.

### Bug 2 — refusal path in `llm.summarize_result` raises `IndexError`, not `APIError`

Fix it at the source, in `llm.py`, mirroring the exact pattern `generate_sql` already uses for refusals: check `response.stop_reason` before indexing into `content`.

```python
if response.stop_reason == "refusal":
    return "Claude declined to summarize this result."
return response.content[0].text
```

This keeps `summarize_result`'s contract intact (always returns a string on a successful API call; only raises `anthropic.APIError` for genuine API failures), so `core.py`'s existing `except anthropic.APIError` guard doesn't need to change at all — the bug is fully contained to `llm.py`.

### Not doing (flagged for your call, not part of this fix)

The reviewer's other three notes are reasonable but optional, so I'd leave them out unless you want them:
- A `PipelineResult` factory/helper to de-duplicate the three construction sites.
- A doc note that `status="success"` doesn't guarantee `answer is not None` (check `error` too).
- A `tests/test_core.py` covering both API-failure paths with mocked `llm`/`database`.

Let me know if you want me to fold any of those three in now, or just ship the two bug fixes.

---

## User

no just fix the bugs. proceed with the plan

---

## Assistant

[Tool: Edit]

---

## Assistant

[Tool: Edit]

---

## Assistant

Now let me verify both fixes with the same reproduction cases the reviewer used.

---

## Assistant

[Tool: Bash]

---

## Assistant

Both repro cases now behave correctly. Let's also confirm the full existing test suite and the earlier smoke test still pass.

---

## Assistant

All 51 tests pass, and both reviewer-confirmed bugs are fixed:

1. **[core.py](core.py)** — `llm.generate_sql(question)` is now wrapped in `try/except anthropic.APIError`, returning `status="error"` with `sql=None` instead of crashing the caller, symmetric with the existing `summarize_result` guard.
2. **[llm.py](llm.py)** — `summarize_result` now checks `response.stop_reason == "refusal"` before indexing into `content`, returning a fixed fallback string instead of raising `IndexError` on a safety refusal.

---

## User

the reviewer flagged this. plan to fix it. don't implement until i approve: One adjacent gap, not one of the two originally flagged — still open
While re-testing I checked the `RuntimeError` `generate_sql` raises on a `max_tokens`-truncated response (a different failure mode from both bugs above — not an `anthropic.APIError`, deliberately raised by `llm.py`’s own `except ValidationError` handling):

```
llm.generate_sql = lambda q: raise RuntimeError("...truncated...")
core.answer_question(...) -> raised uncaught: RuntimeError: ...
```

This still escapes `answer_question()` uncaught, same shape as bug 1 but a different exception type, so the existing `except anthropic.APIError` doesn’t (and shouldn’t, by type) catch it. Not something I called out last time, so this isn’t a regression or an unfixed report — just something worth deciding on now: either widen the catch to `except (anthropic.APIError, RuntimeError)`, or treat a truncated response as a distinct, equally-structured failure mode in `PipelineResult`.
Summary: both reported bugs are correctly fixed and verified by execution, not just inspection. The fixes are reasonably minimal — no new overcomplication introduced.

---

## Assistant

## Plan

Widen the existing guard in `core.py` around the `llm.generate_sql(question)` call to also catch this `RuntimeError`:

```python
try:
    sql_response = llm.generate_sql(question)
except (anthropic.APIError, RuntimeError) as e:
    return PipelineResult(
        question=question, status="error", sql=None, columns=None,
        rows=None, row_count=None, truncated=False, answer=None,
        error=f"Generating SQL failed: {e}",
    )
```

**Why this over a distinct `PipelineResult` status:** `llm.py` only raises `RuntimeError` from `generate_sql` for the truncated-response case — it's not a general-purpose exception that could mask unrelated bugs. From `core.py`'s perspective, "the API call errored out" and "the response came back truncated and unparseable" are the same kind of outcome: SQL generation failed for an operational reason, no SQL was produced, nothing to execute. The existing `status="error"` already captures that. Adding a sixth `Status` value just to distinguish *why* generation failed would be more state for callers to handle without changing what they'd actually do differently.

No changes needed elsewhere — `llm.py`'s truncation handling itself is fine as-is; this is purely about not letting it escape `answer_question()` uncaught.

---

## User

implement and test

---

## Assistant

[Tool: Read]

---

## Assistant

[Tool: Edit]

---

## Assistant

Now let's add a regression test for this and the other two bugs, since no `tests/test_core.py` exists yet.

---

## Assistant

[Tool: Bash]

---

## Assistant

`APIConnectionError(request=None)` works fine as a stand-in (confirmed in earlier manual testing). Now I'll write the test file.

---

## Assistant

[Tool: Bash]

---

## Assistant

All 60 tests pass (9 new in [tests/test_core.py](tests/test_core.py)).

**Fix:** [core.py](core.py) now catches `(anthropic.APIError, RuntimeError)` around the `llm.generate_sql` call, so a truncated/unparseable response (which `llm.py` raises as `RuntimeError`, not an API error) returns `status="error"` instead of escaping uncaught — same outcome as the API-error case, since from the pipeline's perspective both just mean "no SQL was generated."

**Tests added:** [tests/test_core.py](tests/test_core.py) covers the full pipeline with monkeypatched `llm`/`database`, including regression tests for all three previously-fixed bugs (unguarded `generate_sql` API error, the refusal `IndexError` in `summarize_result`, and this `RuntimeError` gap) plus the short-circuit and happy-path branches.

---

## User

reviewer gave this feedback. let's go with option 1. create a plan to implement. do not implement until i approve: Additional bug found: same failure family, 4th trigger not covered
`core.answer_question()` still crashes uncaught — this time with `pydantic.ValidationError` — whenever Claude’s output is valid JSON but violates `SQLResponse`’s cross-field invariant (status/sql/message consistency), as opposed to being outright truncated. This is entirely plausible in practice: the model_validator enforces a semantic rule (e.g. “success” requires non-null `sql`) that JSON-schema structured output alone doesn’t guarantee, so there’s no reason to assume Claude will never trip it.
The pattern here is the same root cause as all three previously-fixed bugs: `generate_sql` can raise several different exception types depending on exactly how the LLM’s output misbehaves, and each fix so far has patched one specific type rather than the general problem. First it was unguarded `anthropic.APIError`, then `IndexError` on refusal, then `RuntimeError` on truncation — each fixed individually, and each time there turned out to be one more.
Worth fixing differently this time, not just extending the list
I’d flag `except (anthropic.APIError, RuntimeError)` itself as the fragile part now, independent of this specific gap: `RuntimeError` is a generic built-in that other, unrelated bugs can also raise — and `llm.get_client()` already raises a plain `RuntimeError` for a missing API key, which this same except clause now also swallows. That’s arguably the wrong behavior: a missing API key is a deployment/config problem that should probably fail loudly and immediately, not get silently converted into a per-question `PipelineResult(status="error", ...)` indistinguishable from a transient network blip.
Two better options than adding a 5th special case to `core.py`’s except list:

1. Have `llm.py` define its own exception (e.g. `class SQLGenerationError(Exception)`), and make `generate_sql` catch every failure mode internally — the truncation `RuntimeError`, the `ValidationError` from the model_validator, etc. — and re-raise all of them uniformly as that one type. `core.py` then only needs `except (anthropic.APIError, llm.SQLGenerationError)`, and any new failure mode discovered later gets fixed in one place (`llm.py`) rather than requiring another round-trip through `core.py`’s except clause.
2. At minimum, add `pydantic.ValidationError` to `core.py`’s except tuple now, and audit whether `get_client()`’s missing-key `RuntimeError` should be special-cased out of the catch-and-degrade-gracefully path.

Either way, I’d treat this as the generate_sql exception surface not being fully enumerated yet, rather than something a 4th patch alone will finish — worth deciding which of the two directions above before the next bug in this family turns up.

---

## Assistant

## Plan

### 1. `llm.py` — add `SQLGenerationError` and make `generate_sql` own its full failure surface

```python
class SQLGenerationError(Exception):
    """Raised when Claude's response can't be turned into a valid SQLResponse
    (truncated JSON, or valid JSON that fails SQLResponse's own invariants)."""
```

Change `generate_sql` so the `try/except` around `client.messages.parse(...)` catches `pydantic.ValidationError` broadly and re-raises **everything** as `SQLGenerationError`, distinguishing the message by cause:

```python
def generate_sql(question: str) -> SQLResponse:
    client = get_client()  # missing-key RuntimeError propagates as-is -- config problem, not a per-question one
    try:
        response = client.messages.parse(...)
    except ValidationError as e:
        if any(err["type"] == "json_invalid" for err in e.errors()):
            raise SQLGenerationError(
                "Claude's response was truncated before it could be parsed as JSON. "
                "Increase max_tokens and retry."
            ) from e
        raise SQLGenerationError(f"Claude's response did not match the expected schema: {e}") from e

    if response.stop_reason == "refusal":
        return SQLResponse(status="unanswerable", sql=None, message="Claude declined to answer this question.")
    return response.parsed_output
```

Key point: `get_client()` is called *outside* this try block (as it already effectively is), so its missing-API-key `RuntimeError` is never caught here — it propagates uncaught out of `generate_sql`, out of `core.answer_question`, loudly, as a startup/config failure should.

### 2. `core.py` — narrow and simplify the except clause

```python
try:
    sql_response = llm.generate_sql(question)
except (anthropic.APIError, llm.SQLGenerationError) as e:
    return PipelineResult(..., status="error", error=f"Generating SQL failed: {e}")
```

Drop the bare `RuntimeError` from the tuple entirely — `llm.SQLGenerationError` now covers every "Claude's output was bad" case, and a missing API key is deliberately left to crash loudly rather than degrade into a per-question `"error"` result.

### 3. Tests (`tests/test_core.py`, maybe a few in `tests/test_llm.py` if one gets added — TBD, can stay in `test_core.py` for now to match current scope)

- Update `test_generate_sql_truncated_response_does_not_crash` to raise `llm.SQLGenerationError` (not raw `RuntimeError`) — matches the new contract.
- Add `test_generate_sql_schema_invariant_violation_does_not_crash`: simulate `generate_sql` raising `llm.SQLGenerationError("...did not match the expected schema...")`, assert `answer_question` returns `status="error"` instead of propagating `pydantic.ValidationError`.
- Add `test_missing_api_key_propagates_uncaught`: simulate `llm.generate_sql` raising a plain `RuntimeError("ANTHROPIC_API_KEY not found...")`, assert it propagates out of `answer_question` uncaught (i.e. `pytest.raises(RuntimeError)`) — proves the deliberate carve-out, not an oversight.

### Net effect

`core.py`'s except clause goes from an ever-growing list of specific exception types to exactly two: real API errors (`anthropic.APIError`) and "Claude's output was bad" (`llm.SQLGenerationError`). Any future failure mode discovered in how Claude's response can misbehave gets fixed once in `llm.generate_sql`, not as another round-trip through `core.py`.

---

## User

implement and test. keep it simple

---

## Assistant

[Tool: Read]

---

## Assistant

Now implementing the plan.

---

## Assistant

[Tool: Edit]

---

## Assistant

Now update `core.py`:

---

## Assistant

Now update the tests:

---

## Assistant

Now let's add a couple of `llm.py`-level tests confirming `generate_sql` wraps both failure modes correctly and doesn't swallow the missing-key error. Let me check if a `tests/test_llm.py` exists already.

---

## Assistant

[Tool: Write]

---

## Assistant

[Tool: Bash]

---

## Assistant

All 65 tests pass. Summary of what changed:

**[llm.py](llm.py)** — added `SQLGenerationError`. `generate_sql` now catches every `ValidationError` from `client.messages.parse` (both truncated-JSON and cross-field invariant violations) and re-raises as `SQLGenerationError` with a cause-specific message. `get_client()`'s missing-API-key `RuntimeError` is called outside that try block, so it still propagates unwrapped — a config problem, not a per-question one.

**[core.py](core.py)** — the except clause is now exactly `(anthropic.APIError, llm.SQLGenerationError)`, replacing the ad-hoc `RuntimeError` catch. Any future "Claude's output was bad" failure mode gets fixed once in `llm.py`, not via another round-trip through `core.py`.

**Tests:**
- [tests/test_llm.py](tests/test_llm.py) (new) — verifies `generate_sql` wraps both truncated-JSON and invariant-violation `ValidationError`s into `SQLGenerationError`, and confirms the missing-API-key `RuntimeError` is *not* wrapped.
- [tests/test_core.py](tests/test_core.py) — updated the truncation test to use `SQLGenerationError`, added a schema-invariant-violation case, and added `test_missing_api_key_propagates_uncaught` proving the config-error carve-out is intentional, not a gap.

---
