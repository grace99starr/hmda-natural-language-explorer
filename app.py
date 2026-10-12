"""
Streamlit UI for the HMDA Natural Language Explorer.

Pure presentation layer: every question is answered by core.answer_question(),
which already wires together NL->SQL generation, validation, and execution.
This file only renders whatever PipelineResult comes back -- it must not
reimplement any of that logic.

Run from the project root: .venv/bin/streamlit run app.py
"""

import pandas as pd
import streamlit as st

import core
import database
import schema

EXAMPLE_QUESTIONS = (
    "What is the average loan amount by loan type?",
    "How many applications were denied in each county?",
    "What were the most common denial reasons?",
)

# Columns whose values are real dollar amounts (not "income", which schema.py
# documents as being in thousands of dollars -- formatting it as a raw dollar
# figure would misrepresent the magnitude).
_CURRENCY_NAME_HINTS = (
    "loan_amount", "property_value", "loan_costs", "points_and_fees",
    "origination_charges", "discount_points", "lender_credits",
    "median_family_income",
)

# Coded columns (action_taken, loan_type, county_code, ...) mapped to their
# label dicts from schema.py. Columns whose "values" entries are empty strings
# (e.g. derived_loan_product_type, where the key IS already the label) are
# included too; _label_value below leaves those untouched.
_CATEGORICAL_LABELS: dict[str, dict] = {c.name.lower(): c.values for c in schema.COLUMNS if c.values}


def _is_currency_column(name: str) -> bool:
    lowered = name.lower()
    return any(hint in lowered for hint in _CURRENCY_NAME_HINTS)


def _label_value(value, labels: dict):
    label = labels.get(value)
    return label if label else value


def _format_results_table(rows: list[tuple], columns: list[str]) -> pd.DataFrame:
    """Build the display dataframe, mapping any column that matches a coded
    HMDA field (by exact name) to its human-readable label. Currency decimal
    formatting is applied separately via column_config at the call site, so
    the underlying values here stay numeric.
    """
    df = pd.DataFrame(rows, columns=columns)
    for col in df.columns:
        labels = _CATEGORICAL_LABELS.get(col.lower())
        if labels:
            df[col] = df[col].map(lambda v: _label_value(v, labels))
    return df


@st.cache_data
def _dataset_record_count() -> int | None:
    """Row count of the live `hmda` table, via the same run_query() entry
    point used for real questions -- not re-derived or hardcoded."""
    result = database.run_query(f"SELECT COUNT(*) FROM {schema.TABLE_NAME}")
    return result.rows[0][0] if result.status == "ok" else None


st.set_page_config(page_title="HMDA Natural Language Explorer", page_icon="\U0001f3e0")

st.title("HMDA Natural Language Explorer")
st.caption("Ask a question in plain English about 2024 HMDA mortgage applications.")

with st.expander("About This Explorer"):
    st.markdown(
        "Explore mortgage lending data using everyday language. Ask a question, "
        "and the tool will generate and execute a SQL query to find an answer."
    )

    st.markdown("**What's in the dataset?**")
    record_count = _dataset_record_count()
    stat_col1, stat_col2 = st.columns(2)
    stat_col1.metric("Reporting year", "2024")
    stat_col2.metric("Mortgage records", f"{record_count:,}" if record_count is not None else "—")
    st.markdown(
        "The dataset contains publicly reported mortgage lending information for "
        "Los Angeles and Santa Barbara counties in California, including loan "
        "amounts, loan types, interest rates, application outcomes, and denial reasons."
    )

    st.markdown("**Example questions**")
    for q in EXAMPLE_QUESTIONS:
        st.markdown(f"- {q}")

    st.markdown("**How it works**")
    st.markdown(
        "Your question is translated into SQL, checked for safety, and executed "
        "against the dataset. You can inspect the generated SQL and underlying "
        "results to verify the answer."
    )

    st.markdown("**Things to know**")
    st.markdown(
        "This tool only covers 2024 data from the two counties listed above. It "
        "cannot answer questions about other years, locations, credit scores, or "
        "mortgage payment history. Some questions may require clarification, and "
        "results depend on how metrics such as approval or denial rates are defined. "
        "For the full definitions of coded fields (e.g. denial reasons, loan types), "
        "see the [FFIEC LAR data field documentation]"
        "(https://ffiec.cfpb.gov/documentation/publications/loan-level-datasets/lar-data-fields)."
    )

with st.form("question_form"):
    question = st.text_input(
        "Your question",
        placeholder="e.g. What is the median loan amount for originated loans in Los Angeles County?",
    )
    submitted = st.form_submit_button("Ask")

if submitted and question.strip():
    try:
        with st.spinner("Asking Claude..."):
            st.session_state["result"] = core.answer_question(question)
    except RuntimeError as e:
        st.session_state["result"] = None
        st.error(f"Configuration error: {e}")
    except Exception as e:
        st.session_state["result"] = None
        st.error(f"Something went wrong while answering this question: {e}")

result: core.PipelineResult | None = st.session_state.get("result")

if result is not None:
    st.subheader(result.question)

    if result.status == "success":
        # st.markdown treats "$...$" as LaTeX math -- escape literal dollar signs
        # so amounts like "$425,000" render as text, not a (broken) equation.
        answer_text = result.answer.replace("$", "\\$") if result.answer else None
        st.markdown(answer_text or "Query succeeded, but no summary could be generated.")

        if result.rows:
            column_config = {
                col: st.column_config.NumberColumn(format="$%,.0f")
                for col in result.columns
                if _is_currency_column(col)
            }
            st.dataframe(_format_results_table(result.rows, result.columns), column_config=column_config)
            if result.truncated:
                st.caption(f"Showing the first {result.row_count} rows; the full result set was larger.")
        else:
            st.info("No results were returned for this query.")

        if result.error:
            st.warning(result.error)

        with st.expander("SQL used"):
            st.code(result.sql, language="sql")

    elif result.status == "clarification_needed":
        st.info(f"Claude needs more detail to answer this: {result.answer}")

    elif result.status == "unanswerable":
        st.warning(f"This question is out of scope for this dataset: {result.answer}")

    elif result.status == "rejected":
        st.error(f"The generated SQL failed a safety check: {result.error}")
        if result.sql:
            with st.expander("SQL that was rejected"):
                st.code(result.sql, language="sql")

    elif result.status == "error":
        st.error(f"Something went wrong: {result.error}")
        if result.sql:
            with st.expander("SQL used"):
                st.code(result.sql, language="sql")
