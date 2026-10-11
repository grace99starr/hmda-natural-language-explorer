# app.py

**Date:** October 11, 2026  
**Messages:** 33

---

## User

explore the repo and its architecture. dont make any changes

---

## Assistant

[Tool: Bash]

---

## Assistant

This is a small, flat Python project. Let me read the key source files to understand the architecture.

---

## Assistant

[Tool: Bash]

---

## Assistant

I've explored the repo without making any changes. Here's the architecture:

## Overview
A small, flat Python project (no package structure) that implements a **natural-language-to-SQL pipeline** over 2024 HMDA (mortgage disclosure) data for LA and Santa Barbara counties, CA. Backed by Claude (Anthropic API) for NL↔SQL translation and DuckDB for execution.

## Data flow
```
question (NL) 
  → llm.generate_sql()      [Claude: NL → structured SQL response]
  → validator.validate_sql() [AST-level safety check via sqlglot]
  → database.run_query()     [DuckDB execution, sandboxed]
  → llm.summarize_result()   [Claude: rows → plain-English answer]
```
Orchestrated end-to-end by [core.py](core.py)'s `answer_question()`, which returns a `PipelineResult` dataclass covering every outcome: `success`, `clarification_needed`, `unanswerable`, `rejected`, `error`.

## Module breakdown
- **[schema.py](schema.py)** — Data dictionary for all 99 HMDA columns (types, coded values, caveats like "cast this VARCHAR column with TRY_CAST, it mixes numbers with 'NA'/'Exempt'"). Single source of truth, rendered into the LLM system prompt (`render_schema_for_prompt()`) and reused by the validator's column allowlist. Also has `validate_schema()` to catch drift between this doc and the live DuckDB table.
- **[llm.py](llm.py)** — Anthropic client wrapper. `generate_sql()` uses `messages.parse()` with a Pydantic `SQLResponse` model (structured output) and a cached system prompt (schema + instructions, ~6K tokens, `cache_control: ephemeral`). `summarize_result()` turns query rows into a short plain-English answer. Handles refusals and truncated/invalid JSON explicitly.
- **[validator.py](validator.py)** — Static AST-based SQL safety gate using `sqlglot`. Enforces: single statement, read-only (SELECT/UNION/INTERSECT/EXCEPT only), only the `hmda` table, only documented columns, and a denylist of environment/file-reading functions (`read_csv`, `getenv`, etc.).
- **[database.py](database.py)** — DuckDB execution layer. Loads the CSV into an in-memory table once (lazy init, thread-locked), with `enable_external_access=False` as defense-in-depth beneath the validator. Caps rows (10K), enforces a 10s query timeout via a watchdog `Timer` + `cursor.interrupt()`, serializes all queries through a single lock.
- **[core.py](core.py)** — Thin glue layer wiring the above into one pipeline, careful to preserve partial results (e.g., keep `sql`/`rows` if only the summarization call fails) and to let config errors (missing API key) propagate uncaught while treating per-question API errors as recoverable.

## Tests ([tests/](tests/))
Solid coverage, each module tested in isolation:
- `test_core.py` — full pipeline via monkeypatched `llm`/`database`, including three documented bug-fix regressions (unguarded API error, refusal IndexError, truncated-response RuntimeError escaping).
- `test_database.py`, `test_llm.py`, `test_validator.py` — unit tests per layer.
- `eval_sample_questions.py` — manual eval script with hand-picked questions expected to hit each status (`success`/`unanswerable`/`clarification_needed`).

## Notable design choices
- Defense-in-depth: SQL safety is enforced at *three* independent layers (validator AST check, DuckDB `enable_external_access=False`, documented-columns allowlist).
- Everything is dataclass/Pydantic-typed with `Literal` status enums rather than strings/exceptions for control flow.
- Data (`data/`), `.venv/`, and `.env` are all gitignored — no secrets or large CSVs committed.
- `requirements.txt` pins exact versions; no `pyproject.toml`/packaging — this is a prototype/exploration-stage project (README confirms: "Initial project setup and data exploration").

No code or files were modified during this exploration.

---

## User

i want to implement app.py which will be a streamlit interface. create a plan to implement the ui. the ui should include natural language question input, plain english answer from core.answer_question() displayed, results table (actual data returned from duckdb), actual sql used to produce that result in an expandable section, clear display of errors like clarification requests, unanswerable questions, technical errors, dataset context. also some kind of mechanism that shows loading when claude is formulating a repsonse. keep it clean and straightforward and simple. do not duplicate backend logic that is already in place. propose a plan and do not implement until i approve

---

## Assistant

[Tool: ToolSearch]

---

## Assistant

[Tool: EnterPlanMode]

---

## Assistant

[Tool: Bash]

---

## Assistant

[Tool: Write]

---

## Assistant

[Tool: ExitPlanMode]

---

## Assistant

Plan approved. Implementing now: creating `app.py` and adding `streamlit` to `requirements.txt`.

---

## Assistant

[Tool: Bash]

---

## Assistant

1.51.0 is valid, matching the pattern of other dependencies in this file (close-to-latest, not bleeding edge). I'll keep it. Now writing `app.py`.

---

## Assistant

Now let's verify the app actually loads without errors.

---

## Assistant

[Tool: Bash]

---

## Assistant

[Tool: Bash]

---

## Assistant

streamlit 1.51.0 caps pandas at `<3`; the latest release (1.65.0) raised that to `<4`, which is compatible with this project's pinned `pandas==3.0.6`. Updating the pin.

---

## Assistant

[Tool: Bash]

---

## Assistant

Installed cleanly. Now let's verify `app.py` imports correctly and run a Streamlit syntax/smoke check.

---

## Assistant

Now let's run the actual Streamlit server and verify it serves correctly.

---

## Assistant

The app serves HTTP 200 with no errors in the logs. Let me check the page content renders the expected elements and then stop the server.

---

## Assistant

[Tool: Bash]

---

## Assistant

Health check is `ok` and no script exceptions in the log — the app loads and runs cleanly. Let's stop the background server now.

---

## Assistant

The app starts cleanly, renders without exceptions, and passes Streamlit's health check. I did not submit an actual question through the UI since that would call the live Anthropic API using your key — let me know if you'd like me to do a full end-to-end test (asks a real question, hits Claude + DuckDB) or if you'll try that yourself.

## Summary

- Added `streamlit==1.65.0` to [requirements.txt](requirements.txt) (bumped from my initial `1.51.0` pin — that version capped `pandas<3`, which conflicted with this project's pinned `pandas==3.0.6`; 1.65.0 allows `pandas<4`).
- Created [app.py](app.py): a presentation-only Streamlit UI that calls `core.answer_question()` and renders every status (`success`, `clarification_needed`, `unanswerable`, `rejected`, `error`) per the approved plan — answer, results table, SQL in an expander, dataset context from `schema.GLOBAL_NOTES`, and a spinner during the call.
- Verified: syntax/compile check passes, and the app boots and serves (HTTP 200, `/_stcore/health` → `ok`, no exceptions in server logs).

To run it: `.venv/bin/streamlit run app.py`.

---

## User

bug found by a reviewer claude. how do you plan to fix it? do not implement until i approve the plan: Correctness
One real bug, confirmed by execution: the error boundary is narrower than what can actually escape `core.answer_question()`.
`app.py` only catches `RuntimeError` around the pipeline call:

```
try:
    with st.spinner("Asking Claude..."):
        st.session_state["result"] = core.answer_question(question)
except RuntimeError as e:
    st.session_state["result"] = None
    st.error(f"Configuration error: {e}")
```

That correctly catches the missing-API-key case (`llm.get_client()`’s `RuntimeError`, deliberately left uncaught by `core.py` by design — confirmed in the last review). But it’s not the only thing that can escape. I checked what happens on a missing dataset — which, given `data/` is gitignored per the take-home’s own requirements, is the state of every fresh clone until someone manually downloads the CSV:

```
database.CSV_PATH = Path('definitely_does_not_exist.csv')
core.answer_question('What is the average loan amount?')
-> NOT a RuntimeError -- app.py would NOT catch this: FileNotFoundError: [Errno 2] No such file or directory: ...
```

`FileNotFoundError` and `RuntimeError` are siblings (both root at `Exception`, not related to each other), so `except RuntimeError` doesn’t catch it. And it’s not just the missing-file case — `database._build_connection()`’s `pd.read_csv(...)` / `duckdb.connect(...)` / `con.execute("CREATE TABLE ...")` chain could raise any number of exception types for a malformed CSV, bad permissions, etc. — none of which are guaranteed to be `RuntimeError`. The very first question asked against a fresh clone without the dataset in place will crash the Streamlit app with a raw traceback instead of a clean error message, which is exactly the scenario this try/except was built to prevent.
Given this is the outermost UI boundary — not a place where narrow catching protects against masking bugs further down, the way it would in `llm.py` or `core.py` — I’d widen this specific catch to `except Exception` (or at least `except (RuntimeError, OSError)` to cover `FileNotFoundError` and friends) and keep the message generic for anything that isn’t the known API-key case.

---

## Assistant

Good catch — that's correct. `database._build_connection()` runs `pd.read_csv()`, `duckdb.connect()`, and `con.execute("CREATE TABLE ...")` with no exception handling of its own, and none of those are guaranteed to raise `RuntimeError`. `app.py`'s `except RuntimeError` only catches the deliberate API-key case; anything else (missing CSV, bad permissions, malformed data, a `duckdb.Error` during table creation) escapes as a raw traceback in the Streamlit app.

## Plan

In [app.py](app.py), widen the single try/except around the `core.answer_question()` call into two:

```python
try:
    with st.spinner("Asking Claude..."):
        st.session_state["result"] = core.answer_question(question)
except RuntimeError as e:
    st.session_state["result"] = None
    st.error(f"Configuration error: {e}")
except Exception as e:
    st.session_state["result"] = None
    st.error(f"Something went wrong while answering this question: {e}")
```

- Keep the specific `RuntimeError` branch and its "Configuration error" message as-is — it's the one documented, known case (missing `ANTHROPIC_API_KEY`), and it's worth a precise message.
- Add a catch-all `except Exception` below it for everything else that can escape the pipeline (missing dataset file, permission errors, malformed CSV, a `duckdb.Error` thrown during connection/table setup, etc.) with a generic message, since this is the outermost UI boundary — there's no deeper caller to hand these to, and showing a raw traceback is worse than a generic error banner.
- Not catching `BaseException` — `Exception` is the right ceiling so `SystemExit`/`KeyboardInterrupt` still propagate normally.

This is a one-location, ~4-line change to `app.py` only — no backend files touched. Want me to go ahead and make this change?

---

## User

yes, go ahead

---

## Assistant

[Tool: Read]

---

## Assistant

[Tool: Edit]

---

## Assistant

Now verifying the fix actually catches the reviewer's reproduction case.

---

## Assistant

Confirmed: `FileNotFoundError` is not a `RuntimeError`, so this exact scenario would have escaped the old handler and is now caught by `except Exception`. Quick sanity check that the app still boots cleanly.

---

## Assistant

Fixed. [app.py](app.py) now has a second `except Exception` branch below the existing `except RuntimeError`, so any escape from `core.answer_question()` — missing dataset, malformed CSV, DuckDB setup errors, etc. — gets a clean error banner instead of a raw traceback, while the specific "Configuration error" message is still preserved for the known missing-API-key case. Verified the fix against the reviewer's exact repro (missing CSV → `FileNotFoundError`) and confirmed it's now caught.

---
