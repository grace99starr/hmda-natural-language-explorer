# database.py

**Date:** October 11, 2026  
**Messages:** 39

---

## User

explore the repo architecture. do not make any changes

---

## Assistant

I'll explore the repository structure and key files to understand the architecture.

---

## Assistant

[Tool: Read]

---

## Assistant

[Tool: Bash]

---

## Assistant

[Tool: Bash]

---

## Assistant

I now have a full picture of the project. Here's the architecture:

## Project: HMDA Natural Language Data Explorer

A natural-language-to-SQL interface over 2024 HMDA (mortgage disclosure) loan data for LA and Santa Barbara counties, built on Claude + DuckDB. Currently in early development (per [README.md](README.md): "Initial project setup and data exploration").

### Components

**[data/hmda_2024.csv](data/hmda_2024.csv)** — ~208K rows, 99 columns, the raw LAR dataset (gitignored, local-only). Scope is hardcoded to 2024 / CA counties 06037 (LA) & 06083 (Santa Barbara).

**[schema.py](schema.py)** — the source of truth for the data dictionary:
- `Column` dataclass (name, SQL type, category, description, coded values, free-text notes) for every column, hand-annotated with HMDA-specific gotchas (text columns masquerading as numbers, sentinel codes, skew warnings, union-needed denial reason columns, etc.)
- `render_schema_for_prompt()` — flattens this into markdown spliced into the LLM system prompt
- `validate_schema()` — cross-checks the dataclass catalog against DuckDB's live `DESCRIBE` output to catch drift between docs and data
- Shared value-code maps (ethnicity, race, AUS, denial reasons) factored out and reused across repeated columns

**[llm.py](llm.py)** — the NL→SQL layer:
- Loads `ANTHROPIC_API_KEY`/`CLAUDE_MODEL` from `.env` (gitignored)
- Builds a system prompt = instructions + `schema.render_schema_for_prompt()`, cached via Anthropic's `cache_control` since it's static ~6K tokens reused every call
- `generate_sql(question)` calls `client.messages.parse()` with a structured `SQLResponse` Pydantic model (`status`: success/clarification_needed/unanswerable, `sql`, `message`), enforced by a `model_validator` that ties field presence to status
- Handles truncated-JSON and refusal edge cases explicitly

**[validator.py](validator.py)** — a defense-in-depth static gate that runs on LLM-generated SQL *before* execution, using `sqlglot` AST parsing (not string matching, so comments/literals can't fool it):
1. Exactly one statement
2. Read-only (SELECT/UNION/INTERSECT/EXCEPT only)
3. Touches only the `hmda` table/documented columns (plus self-defined CTEs/aliases)
4. Blocks an info-disclosure function denylist (`getenv`, `read_csv`, `pragma_*`, etc.)

Explicitly out of scope: it doesn't sandbox actual execution — that's deferred to whatever DuckDB connection config comes later.

**[tests/test_validator.py](tests/test_validator.py)** — pure offline AST tests (no LLM/network) covering valid queries, injection attempts, forbidden tables/functions, CTE edge cases.

**[tests/eval_sample_questions.py](tests/eval_sample_questions.py)** — manual eval script that calls the real `generate_sql()` against hand-picked questions expected to hit each status branch (success/unanswerable/clarification_needed).

**[notebooks/exploration.ipynb](notebooks/exploration.ipynb)** — 33-cell pandas exploration notebook that preceded `schema.py`; this is where the data quirks baked into schema.py's notes were originally discovered (right-skewed loan amounts, denial reason distributions, etc.).

### Data flow
```
question (NL) → llm.generate_sql() [Claude + schema.py prompt] → SQLResponse
                                                                       │
                                                      status=success  │
                                                                       ▼
                                                     validator.validate_sql() [AST gate]
                                                                       │
                                                             (not yet wired up:
                                                              DuckDB execution)
```

The gap: nothing yet wires `llm.py` → `validator.py` → an actual DuckDB execution/results step. That connective layer, plus the `.venv`/execution sandboxing noted in validator.py's docstring, looks like the natural next piece.

---

## User

i want to move on to database.py. based on the project structure, propose a simple plan to implemement the duckdb execution layer. it needs to load hmda_data.csv into a duckdb table named hmda. it needs to restrict access to any external filesystem or network access during execution. it needs to ONLY excecute sql that passes validate_sql() and return results in a structured format to make it easy later when implementing the ui. it needs to handle duckdb execution errors gracefully. it needs to add sensible resource limits. add integration tests. keep the design simple, straightforward, and explanable. give me the plan, do not implement anything yet

---

## Assistant

## Plan for `database.py`

### 1. Module responsibilities
One function is the public surface: `run_query(sql: str) -> QueryResult`. Everything else (connection setup, data load, limits) is internal plumbing that happens once at import/startup.

### 2. Loading the data
Reuse the pattern already in `schema.py`'s `__main__` block — load the CSV with **pandas**, not DuckDB's `read_csv`:

```
df = pd.read_csv("data/hmda_2024.csv", low_memory=False)
con.register("hmda_df", df)
con.execute("CREATE TABLE hmda AS SELECT * FROM hmda_df")
con.unregister("hmda_df")
```

This matters for the lockdown step below: if DuckDB itself never reads the file, we can disable DuckDB's own filesystem/network access *before* any query runs, with no chicken-and-egg problem.

### 3. Locking down the connection
Create the connection with a restrictive config, set once at connect time:

- `enable_external_access=False` — this is the core lockdown flag. It disables `ATTACH`, `COPY`, extension loading/autoinstall, and table functions like `read_csv`/`read_parquet`/`httpfs` (remote or local). This is the real boundary `validator.py` already says it's *not* (its `UNSAFE_FUNCTIONS` list is explicitly "defense in depth... not exhaustive").
- `memory_limit` (e.g. `"2GB"`) and `threads` (e.g. `2`) — sensible caps given the dataset is ~200K rows; prevents a pathological query (e.g. an accidental self cross-join) from exhausting host memory.

Net effect: validator.py = SQL-shape allowlist, database.py config = engine-level enforcement. Two independent layers, neither trusting the other.

### 4. Resource limits beyond memory/threads
- **Timeout:** DuckDB has no native per-query timeout, so run each query on a background thread and call `con.interrupt()` if it's still running after a configurable deadline (e.g. 10s default). Catch the resulting interrupt exception and surface it as a normal error result, not a crash.
- **Row cap:** fetch with `cursor.fetchmany(MAX_ROWS)` (e.g. 10,000) instead of `fetchall()`. If more rows remain, mark the result `truncated=True` rather than silently dropping data or blowing up the response payload — useful for the future UI to show "showing first N of possibly more."

### 5. Validator integration
`run_query()` always calls `validator.validate_sql(sql)` first. Any violation short-circuits to a structured rejection — DuckDB never even sees disallowed SQL. This keeps `database.py` dumb and trusting of one thing only: that `validate_sql` returning `[]` means the query is safe to run.

### 6. Structured result type
A small dataclass (mirrors the `SQLResponse` pattern in `llm.py`), something like:

```
@dataclass
class QueryResult:
    status: Literal["ok", "rejected", "error"]
    columns: list[str] | None
    rows: list[tuple] | None
    row_count: int | None
    truncated: bool
    error: str | None
```

- `status="rejected"` → validator violations, `error` holds the joined violation messages.
- `status="error"` → DuckDB raised during execution (catch the base `duckdb.Error`, plus the interrupt/timeout case) — `error` holds a human-readable message, never the raw traceback.
- `status="ok"` → `columns`/`rows`/`row_count` populated, `truncated` reflects the row cap.

This shape is UI-agnostic (no DuckDB types leak out — e.g. convert `Decimal`/`date` cells to plain Python types) so a future frontend just reads `columns` + `rows`.

### 7. Concurrency note
Use `con.cursor()` per call rather than executing directly on the shared module-level connection, since the timeout/interrupt mechanism and future concurrent UI requests both want isolated cursor state rather than one shared mutable connection.

### 8. Integration tests (`tests/test_database.py`)
No dependency on the real 208K-row CSV (gitignored, may not exist in CI). Instead, build a tiny in-memory fixture inline in the test (a handful of rows as a pandas DataFrame or CSV string written to `tmp_path`), covering:

- **Happy path:** valid SELECT against the loaded `hmda` table returns `status="ok"` with correct columns/rows.
- **Validator rejection:** a query with a non-existent column or non-SELECT statement comes back `status="rejected"` without ever touching DuckDB (could assert via a monkeypatched connection that execute was never called).
- **DuckDB execution error:** a query that's valid per `validate_sql` but fails at runtime in DuckDB (e.g. a type mismatch) comes back `status="error"` with a clean message, no exception propagating out.
- **Lockdown enforcement:** confirm `enable_external_access=False` actually blocks something — e.g. assert `read_csv(...)` raises/fails even though it's also caught by validator's denylist, proving the engine-level boundary works independent of the SQL-shape check.
- **Row cap / truncation:** load a fixture with more rows than a small test-only `MAX_ROWS` override, assert `truncated=True` and `row_count` reflects what was actually returned.
- **Timeout:** trickier to test deterministically — likely a query designed to be slow (e.g. a large self cross-join on the fixture data) against a very short test-only timeout, asserting `status="error"` and a timeout-flavored message. If this proves flaky, it's reasonable to mark it `@pytest.mark.slow` or skip in favor of just unit-testing the interrupt-calling logic in isolation.

### Open question for you
Where should the hardcoded knobs live — `MAX_ROWS`, timeout seconds, `memory_limit`, `threads`? I'd default to module-level constants in `database.py` (same style as `llm.py`'s `DEFAULT_MODEL`), overridable via env vars only if you anticipate needing to tune them per-environment. Let me know if you'd rather they come from `.env` like the Anthropic key does.

---

## User

plan looks good. implement with a few adjustments: you can use global variables within database.py for MAX_ROWS, etc., i don't want to build a complicated concurrency framework. keep execution predictable., you can implement a timeout as long as it's simple and queries wont run in the background after a timeout

---

## Assistant

[Tool: Bash]

---

## Assistant

[Tool: Bash]

---

## Assistant

Good, confirms the lockdown config works and `InterruptException` is a `duckdb.Error` subclass. Let me verify the timeout/interrupt mechanism and row-cap/truncation logic work as expected before writing the real module.

---

## Assistant

[Tool: Bash]

---

## Assistant

[Tool: Bash]

---

## Assistant

The timeout/interrupt mechanism works correctly, and the connection stays usable afterward — no orphaned background execution. Now I'll write `database.py`.

---

## Assistant

[Tool: Write]

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

[Tool: Bash]

---

## Assistant

Good, that confirms the runtime-error test case works. Now let me write the test file.

---

## Assistant

[Tool: Bash]

---

## Assistant

Small bug — missing `error=None` in the success-path `QueryResult`. Let me fix it.

---

## Assistant

[Tool: Bash]

---

## Assistant

All 6 pass. Let's confirm the full suite (including the pre-existing validator tests) still passes, and double check the import of `database.py` itself works cleanly against the real CSV.

---

## Assistant

All 48 tests pass, and manual sanity checks against the real CSV work as expected (import takes ~3s to load/register 208K rows, a valid query returns structured results, an invalid column is rejected pre-execution).

## Summary

[database.py](database.py) — the DuckDB execution layer:
- `_build_connection()` loads the CSV via pandas, then registers it into a DuckDB connection created with `enable_external_access=False`, `memory_limit="2GB"`, `threads=2` — confirmed this blocks `read_csv`/`ATTACH` even inside otherwise-valid SELECT statements.
- `run_query(sql)` runs `validator.validate_sql()` first (rejected queries never touch DuckDB), then executes with a `threading.Timer` watchdog that calls `_con.interrupt()` after `QUERY_TIMEOUT_SECONDS` — verified DuckDB actually cancels in-flight execution and the connection stays usable afterward, not just a dangling background task.
- Results cap at `MAX_ROWS` via `fetchmany` + a trailing `fetchone()` check for `truncated`.
- Returns everything as a plain `QueryResult` dataclass (`status`, `columns`, `rows`, `row_count`, `truncated`, `error`) — no DuckDB types leak out.

[tests/test_database.py](tests/test_database.py) — 6 integration tests, all against tiny in-memory fixtures (never the real CSV): happy path, validator short-circuit (using a fake connection that asserts DuckDB is never touched), a real DuckDB runtime error (bad `CAST`), row-cap truncation, filesystem-access lockdown, and timeout/interrupt behavior including a post-interrupt sanity query to prove the connection isn't left corrupted.

Caught one bug along the way: the success-path `QueryResult` was missing `error=None`, fixed before the final run. Full suite: 48 passed.

---

## User

another claude reviewed your implementation and pointed this out. how would you suggest addressing this? create a plan but do not implement yet. I ran the test suite, then dug into two things the tests’ own docstrings claim but don’t actually prove.
Test suite: all pass, but at a real cost
6/6 tests pass. But timing the import:

```
time python -c "import database"   ->  3.36s total, ~3.3s of it is the CSV load
pytest tests/test_database.py       ->  3.55s total for all 6 tests
```

Over 90% of every test run is `database.py`’s module-level `_con = _build_connection(CSV_PATH)` loading the real 209K-row CSV into pandas and building the DuckDB table — work that every single test immediately discards via `monkeypatch.setattr(database, "_con", ...)`. None of the 6 tests need the real data; all of them pay for it anyway because it happens as an import-time side effect.
Correctness bug: contradicts the test file’s own stated design
tests/test_database.py’s docstring says: “These build small, throwaway DuckDB connections from in-memory fixtures (never the real data/hmda_2024.csv, which is gitignored and may not exist outside this machine).”
That’s true of the tests themselves, but not of the module they import. I verified directly:

```
database._build_connection(Path('definitely_does_not_exist.csv'))
-> FileNotFoundError: [Errno 2] No such file or directory: 'definitely_does_not_exist.csv'
```

Since `_con = _build_connection(CSV_PATH)` runs unconditionally at module level, and `test_database.py` does `import database` at the top, the entire test file fails at collection — before any test body runs — on any machine where data/hmda_2024.csv doesn’t exist. Given that file is gitignored (never committed) and the test docstring explicitly anticipates it being absent “outside this machine,” this is a real gap: a fresh clone or CI run without the dataset can’t run `test_database.py` at all, not even the tests that were specifically designed to not need it.
Test coverage gap: the “second boundary” claim is true but unverified
The module docstring claims `enable_external_access=False` is “a second, independent boundary in case something gets past the validator’s function denylist.” `test_lockdown_blocks_filesystem_access` is clearly meant to test that boundary. But I checked what actually catches that query:

```
validator.validate_sql("SELECT * FROM read_csv('data/hmda_2024.csv')")
-> ["Query references 'READ_CSV(...)'; only the 'hmda' table is allowed.",
    "Function 'read_csv' is not allowed."]
```

The validator rejects it on two separate grounds before it ever reaches DuckDB — so this test never actually exercises the DB-level lockdown it’s named after. I then verified the underlying claim is actually true by bypassing the validator entirely:

```
database._con.execute("SELECT * FROM read_csv('data/hmda_2024.csv') LIMIT 1")
-> PermissionException: Permission Error: Cannot access file "data/hmda_2024.csv" -
   file system operations are disabled by configuration
```

So the security property is real, just not proven by the test suite — the existing test would pass identically even if `enable_external_access` were accidentally deleted from the config. Worth a test that calls `_con.execute()` directly (bypassing `validate_sql`) to actually pin this down.
Overcomplication check: none found
I was suspicious of the hand-rolled `threading.Timer` + `con.interrupt()` timeout — wanted to confirm it’s not reinventing something DuckDB already provides. Checked `duckdb_settings()`:

```
memory_limit                          — a memory cap, not a time cap
pivot_limit                           — unrelated
write_buffer_row_group_memory_limit   — unrelated
```

DuckDB 1.5.6 has no built-in statement-timeout setting, so the manual timer-based cancellation is the standard necessary approach, not over-engineering. Same verdict on the single process-wide connection (no pooling) — a reasonable, explicitly-justified simplicity choice for a single-user tool, not a missing feature.
Minor, unconfirmed
Narrow theoretical race: `timer.cancel()` can’t stop a timer callback that’s already begun running. If a query finishes right at the `QUERY_TIMEOUT_SECONDS` boundary, there’s a timing window where `_con.interrupt()` could fire just after the query completes, on the shared connection, potentially affecting whatever query runs next. I didn’t reproduce this — timing races aren’t practical to force reliably — so flagging it as a theoretical edge case given the shared-connection design, not a confirmed defect. The existing timeout test doesn’t probe this boundary specifically.
Bottom line: the design itself (validator-first, locked-down DuckDB config as defense in depth, timer-based cancellation) is sound and not overcomplicated — I verified the lockdown config actually works and the timeout mechanism isn’t reinventing a wheel. The real issue is the module-level CSV load at import time: it’s slow for every test run and will hard-fail test collection on any machine/CI without the gitignored dataset, which directly contradicts what the test file’s docstring promises.

---

## Assistant

Let me verify two things before writing the plan: that lazy-loading avoids the import-time cost/failure, and whether `con.cursor()` gives us an isolated interrupt scope to kill that race condition too.

---

## Assistant

Good — confirmed both fixes work. Here's the plan (not implementing yet):

## 1. Make the connection lazy, not an import-time side effect

Root cause of both the slowness and the CI-fatal bug: `_con = _build_connection(CSV_PATH)` runs unconditionally the moment `database.py` is imported.

**Fix:** replace the eager module-level build with a lazy singleton.

```python
_con: duckdb.DuckDBPyConnection | None = None

def _get_connection() -> duckdb.DuckDBPyConnection:
    global _con
    if _con is None:
        _con = _build_connection(CSV_PATH)
    return _con
```

`run_query()` calls `_get_connection()` instead of referencing `_con` directly. This fixes both findings at once:
- **Speed:** `import database` no longer touches the CSV. The 3.3s cost only happens on the first real `run_query()` call against the real data — never in tests, since every test monkeypatches `database._con` to a fixture connection *before* calling `run_query`, so the lazy build is skipped entirely (`_con is None` is false).
- **CI/missing-CSV:** collection of `test_database.py` no longer imports a module that eagerly opens a gitignored file. A machine without `data/hmda_2024.csv` can now import `database` and run every test that doesn't need the real file — matching what the test docstring already claims.

This is the one load-bearing change; the other two are smaller, targeted additions.

## 2. Scope the interrupt timer to a per-call cursor, closing the race

Verified with `con.cursor()`: interrupting a cursor only cancels that cursor's query — the parent connection and other cursors are completely unaffected, even when the interrupt fires at the worst possible moment. So instead of timing `_con.interrupt` against the shared connection, `run_query()` should get its own cursor each call:

```python
cursor = _get_connection().cursor()
timer = threading.Timer(QUERY_TIMEOUT_SECONDS, cursor.interrupt)
...
cursor.execute(sql)
```

This isn't connection pooling (no pool, no lifecycle management, still one shared underlying DB) — it's a one-line change that makes each `run_query()` call's cancellation self-contained, so a late-firing timer can never bleed into whatever query runs next on the shared connection. Directly closes the "narrow theoretical race" the review flagged, without adding the concurrency framework you said you don't want.

## 3. Add a test that actually proves the DB-level lockdown

`test_lockdown_blocks_filesystem_access` currently goes through `run_query()`, so the validator's table/function denylist catches `read_csv(...)` first — the DuckDB `enable_external_access=False` boundary is never exercised. Two changes:
- Rename the existing test to make clear what it covers (validator-level rejection), e.g. `test_validator_blocks_read_csv_before_execution`.
- Add a new test that bypasses `validate_sql` entirely and calls the fixture connection's `.execute()` directly with the dangerous SQL, asserting it raises (a `duckdb.Error`/`PermissionException`). This pins down the actual claim in the module docstring — that the lockdown config is an independent second boundary — by proving it would catch the query even if the validator didn't exist.

## 4. One new regression test for the lazy-load fix itself

Add a test that monkeypatches `database._con` back to `None` and `database.CSV_PATH` to a nonexistent path, then asserts calling `run_query()` (or `_get_connection()`) raises a clear `FileNotFoundError` at that point — proving the deferred failure happens predictably on first use rather than silently or at import time.

---

Net effect: `test_database.py` collection no longer depends on the real CSV being present, the whole suite drops from ~3.5s to near-instant, the lockdown boundary gets a test that actually exercises it, and the shared-connection interrupt race is closed with a one-line change rather than new infrastructure. Want me to implement this?

---

## User

yes, implement the plan. change one thing: since we are sharing one duckbd connection, add a lock so only one query can run at a time. this would also avoid two threads initializing the database at the same time. keep the implementation simple and easy to understand

---

## Assistant

[Tool: Read]

---

## Assistant

Now rewriting with lazy init, the lock, and per-call cursors:

---

## Assistant

Now let's update the test file: fixtures need to set `database._con` (still works the same), plus rename/add tests per the plan.

---

## Assistant

[Tool: Edit]

---

## Assistant

[Tool: Edit]

---

## Assistant

[Tool: Bash]

---

## Assistant

All 9 tests pass, and total runtime dropped from ~3.5s to ~0.7s — confirming the import-time cost is gone. Let's also sanity-check that `import database` alone doesn't touch the CSV, run the full suite, and verify the lock actually serializes concurrent calls.

---

## Assistant

[Tool: Bash]

---

## Assistant

Confirmed: 5 concurrent threads all got correct, consistent results, executed one-at-a-time (no corruption, no interleaving) — exactly the serialization the lock is meant to guarantee.

## Summary

Changes to [database.py](database.py):
1. **Lazy connection** — `_con` starts as `None`; `_get_connection()` builds it only on first real use inside `run_query()`. Import no longer touches the gitignored CSV, fixing both the slow test suite and the hard-fail-at-collection bug on machines without the dataset.
2. **Global lock** — `_lock` wraps the connection-get + execute + fetch in `run_query()`, so only one query (and, as a side effect, only one connection-build) runs at a time process-wide. Verified with 5 concurrent threads: fully serialized, correct results, no corruption.
3. **Per-call cursor** — `_get_connection().cursor()` instead of operating on the shared connection directly, so a timer that fires a moment late can only ever cancel its own call's cursor, never bleed into the next query (closes the race flagged in review, verified independently with `cursor.interrupt()` tests).

Changes to [tests/test_database.py](tests/test_database.py) — now 9 tests, suite time dropped from ~3.5s to ~0.7s:
- Renamed the old lockdown test to `test_validator_blocks_read_csv_before_execution` (accurately describes what it actually tests).
- Added `test_lockdown_blocks_filesystem_access_even_if_validator_is_bypassed`, which calls the connection directly, skipping `validate_sql`, to actually prove the `enable_external_access=False` boundary works independent of the validator.
- Added `test_connection_is_not_built_until_first_use` and `test_missing_csv_fails_clearly_on_first_use_not_at_import`, regression-testing the lazy-load fix itself.

Full suite: 51 passed in 0.61s.

---
