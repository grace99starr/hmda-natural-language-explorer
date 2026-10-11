"""
Static validator for LLM-generated DuckDB SQL.

Independently checks a candidate SQL string -- before it is ever executed --
against three rules: it must be a single statement, it must be read-only,
and it must touch only the documented `hmda` table and columns (schema.py
is the source of truth for the latter). Implemented as an AST check via
sqlglot rather than string/keyword matching, so SQL comments and string
literals that happen to contain forbidden keywords can't fool it.

Out of scope: this module only checks the *shape* of the SQL text. It does
not sandbox execution (e.g. restricting DuckDB's filesystem/network access
at the connection level) -- that will be handled separately when the DuckDB
connection used to run validated queries is configured.
"""

import sqlglot
from sqlglot import expressions as exp
from sqlglot.errors import ParseError

import schema

DIALECT = "duckdb"

# Root node types that count as "read-only". `WITH ... SELECT` and set
# operations (UNION/INTERSECT/EXCEPT) over SELECTs parse to one of these;
# anything else (INSERT, DROP, ATTACH, PRAGMA, COPY, ...) is rejected simply
# by not matching.
READ_ONLY_STATEMENTS = (exp.Select, exp.Union, exp.Intersect, exp.Except)

DOCUMENTED_COLUMNS = {c.name.lower() for c in schema.COLUMNS}

# Functions that leak environment/session/system info or read external
# files. Kept separate from the table allowlist below because these can
# appear anywhere in an expression, not just in a FROM clause. Not
# exhaustive -- the table allowlist is the real boundary; this list is
# defense in depth against the most obvious information-disclosure calls.
UNSAFE_FUNCTIONS = {
    "getenv",
    "current_setting",
    "current_database",
    "current_schema",
    "current_version",  # version()
    "pragma_version",
    "pragma_platform",
    "pragma_database_list",
    "pragma_table_info",
    "setseed",
    "read_csv",
    "read_parquet",
    "read_json",
    "read_ndjson",
    "read_text",
    "read_blob",
    "glob",
    "sqlite_scan",
    "postgres_scan",
    "iceberg_scan",
    "delta_scan",
}


def _function_key(func: exp.Func) -> str:
    """Normalize a parsed function call to a lowercase name for denylist lookup."""
    if isinstance(func, exp.Anonymous):
        return str(func.this).lower()
    return func.sql_name().lower()


def validate_sql(sql: str) -> list[str]:
    """
    Validate `sql` against three rules: exactly one statement, read-only
    (SELECT, or UNION/INTERSECT/EXCEPT of SELECTs, optionally with CTEs),
    and access restricted to the `hmda` table and its documented columns
    (plus whatever aliases/CTEs the query defines for itself).

    Returns a list of human-readable violations; an empty list means the
    query passed and is safe to execute.
    """
    try:
        statements = [s for s in sqlglot.parse(sql, read=DIALECT) if s is not None]
    except ParseError as e:
        return [f"SQL failed to parse: {e}"]

    if len(statements) != 1:
        return [f"Expected exactly one SQL statement, found {len(statements)}."]

    statement = statements[0]
    if not isinstance(statement, READ_ONLY_STATEMENTS):
        return [f"Only read-only SELECT queries are allowed; found a '{statement.key}' statement."]

    violations = []

    cte_names = {cte.alias.lower() for cte in statement.find_all(exp.CTE)}
    allowed_tables = {schema.TABLE_NAME.lower()} | cte_names
    seen_tables = set()
    for table in statement.find_all(exp.Table):
        if table.db or table.catalog or table.name.lower() not in allowed_tables:
            ref = table.sql(dialect=DIALECT)
            if ref not in seen_tables:
                seen_tables.add(ref)
                violations.append(f"Query references '{ref}'; only the '{schema.TABLE_NAME}' table is allowed.")

    allowed_columns = (
        DOCUMENTED_COLUMNS
        | cte_names
        | {a.alias.lower() for a in statement.find_all(exp.Alias) if a.alias}
    )
    seen_columns = set()
    for column in statement.find_all(exp.Column):
        name = column.name
        if name == "*" or name.lower() in allowed_columns:
            continue
        if name.lower() not in seen_columns:
            seen_columns.add(name.lower())
            violations.append(f"Unknown column '{name}'; not documented in schema.py.")

    seen_funcs = set()
    for func in statement.find_all(exp.Func):
        key = _function_key(func)
        if key in UNSAFE_FUNCTIONS and key not in seen_funcs:
            seen_funcs.add(key)
            violations.append(f"Function '{key}' is not allowed.")

    return violations
