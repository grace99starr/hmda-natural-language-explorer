# HMDA Natural Language Data Explorer

A tool that lets someone ask a natural language question about 2024 Home Mortgage
Disclosure Act (HMDA) lending data for Los Angeles and Santa Barbara counties, CA
(~209,000 applications), and get back a plain-English answer, the underlying rows,
and the exact SQL query that produced them.

## Demo

- **Screen recording:** _TODO — add a link here once recorded (e.g. OBS → YouTube/Drive/Loom link)._
- **Architecture diagram:** [HMDA Explorer Architecture (Lucidchart)](https://lucid.app/lucidchart/22330c5d-241f-4a70-ab77-da6a5f1793c7/edit)
- **Written explanation of approach + alternative considered:** _TODO — kept as a separate deliverable from this README; link it here once it's in the repo._

## What it does

Ask a question like:

- "What were the main denial reasons?"
- "What is the average loan amount by loan type?"
- "What percentage of applications were denied in each county?"

The app turns the question into a single read-only SQL query against the HMDA
data, validates that query before running it, executes it, and returns three
things together: a plain-English answer, the result rows, and the SQL that was
run — so the answer is always verifiable against the numbers.

## Architecture

```
User → app.py (Streamlit UI) → core.py (orchestrator)
     → llm.py: generate_sql()         [Claude: question → structured SQL]
     → database.py: run_query()
         → validator.py: validate_sql()   [AST safety gate]
         → DuckDB execution (sandboxed, in-memory, capped, timed out)
     → llm.py: summarize_result()     [Claude: rows → plain-English answer]
     → back through core.py → app.py → User
```

See the [Lucidchart diagram](https://lucid.app/lucidchart/22330c5d-241f-4a70-ab77-da6a5f1793c7/edit)
for the full picture, including the rejected-query and unanswerable/clarification
paths.

Module responsibilities:

| Module | Responsibility |
|---|---|
| [`app.py`](app.py) | Streamlit UI. Pure presentation layer — renders whatever `core.answer_question()` returns; does not reimplement any pipeline logic. |
| [`core.py`](core.py) | Orchestrates the pipeline end to end and returns a single `PipelineResult` (status, sql, rows, answer, error). |
| [`llm.py`](llm.py) | Anthropic client wrapper. `generate_sql()` turns a question into structured SQL (or a clarification/unanswerable response); `summarize_result()` turns query results into a plain-English answer. |
| [`validator.py`](validator.py) | Independent, AST-based (`sqlglot`) static check on LLM-generated SQL: single statement, read-only, only the documented table/columns, no unsafe functions. Runs *before* any SQL reaches DuckDB. |
| [`database.py`](database.py) | Executes validated SQL against an in-memory DuckDB instance locked down with `enable_external_access=False`, with a row cap and a query timeout. |
| [`schema.py`](schema.py) | Single source of truth: a 99-column data dictionary (types, coded values, data-quality notes) built from the CSV header and the [FFIEC LAR field documentation](https://ffiec.cfpb.gov/documentation/publications/loan-level-datasets/lar-data-fields). Feeds the LLM's system prompt, the validator's column allowlist, and the UI's code→label mapping. |

## Running it

### 1. Set up the environment

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

### 2. Add your API key

Create a `.env` file in the project root (gitignored, never committed):

```
ANTHROPIC_API_KEY=sk-ant-...
# optional, defaults to claude-sonnet-4-6
CLAUDE_MODEL=claude-sonnet-4-6
```

### 3. Add the data

Download the 2024 HMDA LAR data for Los Angeles and Santa Barbara counties and
place it at `data/hmda_2024.csv`. The dataset is not committed to this repo (see
`.gitignore`). Field definitions are in the
[FFIEC LAR data dictionary](https://ffiec.cfpb.gov/documentation/publications/loan-level-datasets/lar-data-fields).

### 4. Run the app

```bash
.venv/bin/streamlit run app.py
```

### 5. Run the tests

```bash
.venv/bin/pytest
```

All 65 unit tests mock the Anthropic client and DuckDB where relevant, so they
run offline with no API key and no data file required.

### 6. Run the evaluation suite

```bash
.venv/bin/python evaluation/evaluate.py
```

This makes real Claude API calls (one per test question) and requires both
`ANTHROPIC_API_KEY` and `data/hmda_2024.csv`. See [Evaluation](#evaluation) below.

## LLM used

**Claude (`claude-sonnet-4-6`, via the Anthropic API)**, configurable through the
`CLAUDE_MODEL` environment variable. Chosen because:

- Strong structured-output support (`client.messages.parse` against a Pydantic
  model) made it straightforward to get a reliable `{status, sql, message}`
  response shape instead of parsing free text.
- Prompt caching (`cache_control: ephemeral`) lets the ~6K-token schema
  description be cached across requests instead of re-sent on every question.
- Good instruction-following on the kind of constrained, schema-grounded SQL
  generation this task needs (quoting hyphenated columns, following the
  denial-reason/action-taken conventions documented in `schema.py`, declining
  out-of-scope questions instead of guessing).

## Evaluation

`evaluation/cases.json` defines 20 test questions across four categories, each
with an expected pipeline *status*; `evaluation/reference_queries.sql` holds
developer-authored ground-truth SQL for the 10 answerable cases.
`evaluation/evaluate.py` runs every case through the real pipeline and writes a
full side-by-side record (generated SQL + rows vs. reference SQL + rows) to
`evaluation/results.json`.

**Current results: 19/20 (95%) status-match accuracy.**

| Category | Status accuracy | What it tests |
|---|---|---|
| Basic (5) | 5/5 (100%) | Simple filters/aggregates/GROUP BY on single-table queries |
| Complex (5) | 5/5 (100%) | Multi-condition filters, derived ratios, `TRY_CAST` on text-typed numeric columns, unioning the four `denial_reason-N` columns |
| Unanswerable (5) | 5/5 (100%) | Out-of-scope year/county, fields HMDA doesn't disclose (e.g. exact credit score), concepts absent from the schema entirely |
| Ambiguous (5) | 4/5 (80%) | Questions needing a `clarification_needed` response because a term ("best", "successful", "typical") is undefined |

**Honest caveats on this number:**

- Scoring is **status-match only** — did the pipeline return the expected
  `success` / `clarification_needed` / `unanswerable` status? Whether a
  `success` case's generated SQL actually answers the question correctly is
  *not* automated; it's left for a human to eyeball by comparing the generated
  SQL/rows against the reference SQL/rows in `results.json`, since two queries
  can legitimately disagree in shape (extra columns, different grouping, a
  differently-defined rate) without either being wrong.
- The one miss: *"Which applicants were high risk?"* was expected to trigger
  `clarification_needed` (no defined risk threshold) but the model returned
  `unanswerable` instead. Arguably a defensible call either way — there's no
  "risk" field in the schema at all, which is also a legitimate reason to
  reject the question outright — but it didn't match the expected label.
- 20 cases is a small sample. It's enough to sanity-check each of the four
  response modes the pipeline can produce, not a statistically rigorous
  benchmark.

## Known limitations

- **Messy source columns.** Several numeric-looking HMDA fields (`interest_rate`,
  `rate_spread`, `loan_to_value_ratio`, `property_value`, etc.) are stored as
  text because they mix real values with sentinel strings (`"NA"`, `"Exempt"`).
  `debt_to_income_ratio` is worse: some rows are exact integer percentages as
  strings, others are pre-binned ranges (`"30%-<36%"`), and there's no single
  numeric cast that works for both. `schema.py` documents these quirks for the
  LLM, but a sufficiently novel phrasing could still trip over them.
- **`loan_amount` is heavily right-skewed** by a handful of large
  multifamily/commercial loans (some >$100M) against a median around $425K.
  The model is instructed to prefer median over mean for "typical loan" style
  questions, but this depends on the LLM following that instruction correctly
  on a given question, not a structural guarantee.
- **Validator is defense-in-depth, not a provable sandbox.** `validator.py`'s
  AST check enforces single-statement, read-only, table/column allowlisting,
  and a function denylist — but the denylist is explicitly *not exhaustive*.
  The real backstop is the DuckDB connection itself being configured with
  `enable_external_access=False`, so even a query that slipped past the
  validator can't touch the filesystem or network.
- **No concurrency.** One process-wide DuckDB connection, serialized by a
  single lock — only one query runs at a time. Fine for a single-user demo,
  not built for concurrent users.
- **Every question costs two Claude calls** (SQL generation, then result
  summarization), so each question has real latency (a few seconds) and a
  small API cost. There's no caching of repeated/similar questions.
- **No conversation memory.** Each question is answered independently; the
  tool can't resolve a follow-up like "what about just for FHA loans?"
  without the original question being restated.
- **Row cap and timeout are fixed,** not surfaced as user-configurable
  settings: results are capped at 10,000 rows and queries are aborted after
  10 seconds server-side.

## AI assistance

This project was built with help from Claude Code (Anthropic's CLI coding
agent). Session history is in [`ai-history/`](ai-history/).

## Repo structure

```
app.py          Streamlit UI
core.py         Pipeline orchestrator
llm.py          Claude client: NL -> SQL, results -> plain English
validator.py    Static SQL safety check (sqlglot AST)
database.py     Sandboxed DuckDB execution
schema.py       HMDA column catalog (source of truth)
tests/          Unit tests (offline, mocked)
evaluation/     End-to-end eval harness + 20 labeled test questions
notebooks/      Initial data exploration
ai-history/     AI coding assistant session history
data/           hmda_2024.csv goes here (gitignored, not committed)
```
