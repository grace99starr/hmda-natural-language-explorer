# validator.py

**Date:** October 11, 2026  
**Messages:** 24

---

## User

explore and understand the repo. don't make any changes

---

## Assistant

I'll explore the repository structure and key files to understand what this project does.

---

## Assistant

[Tool: Read]

---

## Assistant

[Tool: Bash]

---

## Assistant

[Tool: Read]

---

## Assistant

I now have a complete picture of the repo. No changes made — this was exploration only.

## Summary

**HMDA Natural Language Data Explorer** — an early-stage project that lets users ask natural-language questions about 2024 Home Mortgage Disclosure Act (HMDA) loan data and get back SQL + answers, via Claude.

**Structure:**
- [schema.py](schema.py) — the core artifact. A hand-built data dictionary for all 99 columns of the HMDA dataset (identifiers, loan classification, pricing, applicant demographics, denial reasons, census-tract context), including coded-value lookups, data quality notes (e.g. which "numeric" columns are actually messy text with "NA"/"Exempt" sentinels), and domain conventions (e.g. `action_taken = 1` means originated, `= 3` means denied). It renders this as markdown for the LLM system prompt (`render_schema_for_prompt`) and can cross-validate itself against the live DuckDB table (`validate_schema`).
- [llm.py](llm.py) — the NL→SQL layer. Wraps the Anthropic client (model `claude-sonnet-4-6`), uses `schema.py`'s rendered schema (cached via `cache_control`) as system prompt context, and asks Claude to return a structured `SQLResponse` (status: `success`/`clarification_needed`/`unanswerable`, plus `sql` and `message`) via `client.messages.parse` with a Pydantic model and cross-field validation.
- [tests/eval_sample_questions.py](tests/eval_sample_questions.py) — manual eval script with 6 sample questions covering all three expected statuses (answerable, out-of-scope, ambiguous).
- [notebooks/exploration.ipynb](notebooks/exploration.ipynb) — the exploratory groundwork behind `schema.py`: loads `data/hmda_2024.csv` (208,881 rows × 99 columns, gitignored, LA + Santa Barbara counties only), profiles missingness, investigates outliers (e.g. $438M loans are multifamily/commercial), and prototypes DuckDB queries (avg loan by type, denial reasons) that fed directly into the schema notes and conventions.
- Local `.venv` has `anthropic`, `duckdb`, `pandas`, `pydantic`, `python-dotenv`, `jupyter` installed; no `requirements.txt`/`pyproject.toml` committed yet. Secrets live in a gitignored `.env` (`ANTHROPIC_API_KEY`, optional `CLAUDE_MODEL`).

**State:** per the README, still "initial setup and data exploration," but in practice the NL→SQL pipeline (schema + prompt + structured output + validation) is already functional; only a thin eval script exists, no UI/CLI entry point yet.

---

## User

im implementing the next component of NL to SQL application. i want validator.py to independently validate llm generated duckdb sql before it's executed. it should enforce read only queries, a single sql statement, and access only to the hmda data and documented columns within it. before writing any code, propose an implementation plan, explain the approach and limitations, and identify the tests we should write. don't modify anything until i approve. keep the design simple

---

## Assistant

[Tool: Bash]

---

## Assistant

## Proposed plan for `validator.py`

### Approach

Use **`sqlglot`** (parse `read="duckdb"`) to turn the LLM's SQL text into an AST and check it against three independent rules, rather than regex/keyword matching on raw text. Regex-based checks are easy to defeat (comments, string literals, quoting) and easy to get wrong in the safe direction too — a real parser gives a structural guarantee.

The table/column restriction is enforced as an **allowlist**, not a denylist: the only valid `FROM`/`JOIN` source is the literal `hmda` table (or a CTE alias defined earlier in the same query). This has a nice side effect — it blocks DuckDB's file-scan shorthand (`FROM 'x.csv'`), table-valued functions (`read_csv`, `read_parquet`, `glob`, `iceberg_scan`, …), and cross-database references all at once, without needing to enumerate dangerous function names one by one.

### Validation pipeline

`validate_sql(sql: str) -> list[str]` — mirrors the existing `schema.validate_schema()` convention: empty list = valid, non-empty = human-readable violations (no exceptions for expected failure modes).

1. **Parse.** `sqlglot.parse(sql, read="duckdb")`. A `ParseError` becomes a single violation message. Drop trailing empty statements from a trailing `;` so `SELECT 1;` isn't flagged as two statements.
2. **Single statement.** After dropping trailing empties, exactly one statement must remain.
3. **Statement type allowlist.** The root node must be `SELECT`/`UNION`/`INTERSECT`/`EXCEPT` (covers `WITH ... SELECT` and the denial-reason union pattern called out in `schema.py`). Anything else (`INSERT`, `UPDATE`, `DELETE`, `CREATE`, `DROP`, `ALTER`, `ATTACH`, `COPY`, `PRAGMA`, `CALL`, `SET`, `INSTALL`/`LOAD`, transaction statements) is rejected by not matching, with no need to enumerate them.
4. **Table allowlist.** Walk all `FROM`/`JOIN` sources. Each must resolve to the literal table name `hmda` (case-insensitive, unqualified — no `catalog.hmda` or `other_db.hmda`) or a CTE alias defined earlier in the same statement. Self-joins on `hmda` are fine. Anything else — another table name, a table function call, a string/file literal — is rejected.
5. **Column allowlist (defense in depth).** Collect documented column names from `schema.COLUMNS`, plus every alias introduced in the query itself (`SELECT ... AS x`, CTE names). Every `exp.Column` reference must be in that combined set. This is best-effort, not airtight scope resolution — see limitations — but it also doubles as a drift check against `schema.py`, similar in spirit to `validate_schema()`.
6. **Scalar function denylist (defense in depth).** A short, explicit list of non-table-returning functions that leak environment/system info or mutate session state — e.g. `getenv`, `current_setting`, `current_database`, `version`, `pragma_version`, `pragma_database_list`, `setseed`. These can't be caught by the table allowlist since they're valid anywhere an expression appears, not just in `FROM`.

### Where it plugs in

Call `validator.validate_sql(result.sql)` in between `llm.generate_sql()` returning `status="success"` and actually executing against DuckDB. Non-empty violations should be treated the same as an `unanswerable`/error response — not retried or auto-fixed, since that'd be a separate "repair loop" feature.

### Limitations (worth stating up front)

- This is **static structural validation**, not a sandbox. It catches a lot, but defense-in-depth at the execution layer (e.g. a DuckDB connection created with `enable_external_access=False`, which disables file/network access engine-wide) is a good complement — that belongs in whatever module actually calls `con.sql(...)`, not here, unless you want it folded into this file.
- Column-reference checking (#5) is heuristic, not full scope-accurate binding — it won't catch an alias "leaking" across unrelated subquery scopes. That's a correctness gap, not a security one, since rule #4 already guarantees there's no table but `hmda` to pull extra data from.
- The scalar-function denylist (#6) is necessarily non-exhaustive; new DuckDB functions could appear that we haven't listed. The table allowlist (#4) is the real security boundary; #5 and #6 are belt-and-suspenders.
- `sqlglot`'s DuckDB dialect could theoretically diverge from DuckDB's actual grammar on some edge case. Any divergence should fail closed (reject), which is the safe direction.

### New dependencies

- `sqlglot` (runtime) — no `requirements.txt`/`pyproject.toml` exists yet, so I'd add one or just note the `.venv` install; your call.
- `pytest` (dev/test only) — `eval_sample_questions.py` is a manual print-script because it depends on a live LLM call; `validator.py` is pure deterministic logic with no external calls, so real `assert`-based tests pay off here. Happy to keep it script-style instead if you'd rather not add pytest.

### Tests (`tests/test_validator.py`)

**Valid queries pass:**
- Simple `SELECT` with documented columns, `WHERE`/`GROUP BY`/`ORDER BY`
- Aggregates (`COUNT`, `AVG`, `MEDIAN`/`quantile_cont`)
- `WITH ... SELECT` (CTE)
- `TRY_CAST(... ) AS x` then referencing `x` later in the same query
- Quoted hyphenated columns (`"denial_reason-1"`, `"co-applicant_sex"`)
- `UNION`/`UNION ALL` of two valid `hmda` queries (the denial-reason-1..4 pattern)
- `SELECT *`
- Self-join (`FROM hmda a JOIN hmda b ON ...`)
- Case-insensitive table name (`FROM HMDA`, `From Hmda`)

**Rejected — multiple statements:**
- `SELECT 1; DROP TABLE hmda;`
- `SELECT 1; SELECT 2;`
- A single trailing `;` must still **pass** (regression guard against over-rejecting)

**Rejected — non-SELECT statement types:**
- `INSERT`, `UPDATE`, `DELETE`, `DROP TABLE`, `CREATE TABLE`, `ALTER TABLE`, `ATTACH`, `COPY`, `PRAGMA`, `CALL`, `SET`, `INSTALL`/`LOAD`

**Rejected — table access:**
- `FROM other_table`
- `FROM read_csv('/etc/passwd')`
- `FROM 'file.parquet'`
- `FROM some_other_db.hmda`

**Rejected — columns:**
- `SELECT nonexistent_column FROM hmda`
- `... WHERE fake_col = 1`

**Rejected — denylisted scalar functions:**
- `SELECT getenv('PATH') FROM hmda`
- `SELECT current_setting('x') FROM hmda`

**Edge cases:**
- Comment containing a forbidden keyword (`SELECT 1 FROM hmda -- DROP TABLE`) must **pass** — proves we check the AST, not raw text
- String literal containing SQL keywords (`SELECT 'DROP TABLE' AS note FROM hmda`) must **pass**
- Empty string / whitespace-only input → clean violation message, no crash
- Unparseable garbage SQL → clean violation message, no crash

---

Let me know if you want any of the open calls changed (sqlglot vs. no-new-dependency, pytest vs. script-style tests, whether execution-layer sandboxing belongs in this file), and I'll adjust before writing anything.

---

## User

use pytest for deterministic tests. include valid queries, invalid columns, multiple statements, prohibited table access, unsafe functions, and cte edge cases. add a basic requirements.txt with the dependencies for the project so far. we will handle database security when we configure duckdb later. implement the validator and run the test and report failures or limitations. prioritize correctness and simple code so that it's explainable in my interview

---

## Assistant

[Tool: Bash]

---

## Assistant

Now let me check sqlglot's AST shape for the tricky cases (table functions, file literals, CTEs) before writing the validator logic.

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

[Tool: Bash]

---

## Assistant

[Tool: Bash]

---

## Assistant

[Tool: Bash]

---

## Assistant

Good — now I understand sqlglot's AST well enough for all the edge cases (table functions, CTEs, comments, string literals, qualified names). Let me write the files.

---

## Assistant

[Tool: Write]

---

## Assistant

Now the test file:

---

## Assistant

Now let's run the tests:

---

## Assistant

All 42 tests pass. Summary of what was built:

## Files added
- **[requirements.txt](requirements.txt)** — pinned to what's actually installed: `anthropic`, `duckdb`, `pandas`, `pydantic`, `python-dotenv` (existing), plus new `sqlglot` (SQL parsing) and `pytest` (testing).
- **[validator.py](validator.py)** — `validate_sql(sql: str) -> list[str]`, same "empty list = valid" convention as `schema.validate_schema()`. Pipeline:
  1. Parse with `sqlglot` (DuckDB dialect). Parse error → one violation.
  2. Must be exactly one statement (trailing `;` is fine, stacked statements aren't).
  3. Root node must be `Select`/`Union`/`Intersect`/`Except` — this single `isinstance` check rejects every DDL/DML statement type (`INSERT`, `DROP`, `ATTACH`, `PRAGMA`, `COPY`, …) without enumerating them.
  4. **Table allowlist**: every `FROM`/`JOIN` target must resolve to the literal `hmda` table or a CTE defined earlier in the same query. This one rule also catches DuckDB's file-scan shorthand (`FROM 'x.parquet'`) and table functions (`read_csv`, `sqlite_scan`, …) for free, since their parsed name doesn't match `hmda`.
  5. **Column allowlist**: every column reference must be a documented `schema.py` column or an alias/CTE name the query defines for itself.
  6. **Function denylist** (defense in depth): catches env/system-info and file-reading functions (`getenv`, `current_setting`, `read_csv`, …) anywhere in the expression tree, not just in `FROM` position.
- **[tests/test_validator.py](tests/test_validator.py)** — 42 pytest cases, parametrized by category: valid queries (incl. comments/string-literals with keywords, the denial-reason UNION pattern, self-joins, case-insensitive table names), undocumented columns, multiple statements, non-SELECT statement types, prohibited table access, unsafe functions, and CTE edge cases (alias reuse, chained CTEs, a bad table hidden inside a CTE body, a bad table mixed alongside a valid CTE in the outer query).

## Why sqlglot, briefly
I used sqlglot's parser to empirically verify tricky cases before writing the logic (DuckDB table functions parse as `Table(this=ReadCSV(...))` with an empty `.name`, so they fail the allowlist automatically; comments attach as metadata, never as executable nodes; string literals never produce `Column`/`Table` nodes) — this is the "explain to an interviewer" story: it's an AST allowlist, not pattern matching, so comments/quoting tricks can't fool it.

## Limitations (stated in the module docstring too)
- Static shape-checking only — not a sandbox. You said DB-level security (e.g. `enable_external_access=False`) is deliberately deferred to when the DuckDB connection is configured.
- Column allowlist is a flat set, not true per-scope binding — it can't catch an alias "leaking" across unrelated subquery scopes. Not a security gap (rule 4 already guarantees the only real table is `hmda`), just a correctness nuance worth knowing if asked.
- The function denylist is a representative list, not exhaustive — new DuckDB functions could appear uncovered. The table allowlist is the real boundary; this list is best-effort.
- No test runs against a live DuckDB connection — these are pure parse-tree tests, so they say nothing about whether valid-looking SQL actually executes correctly against the real `hmda` table.

---
