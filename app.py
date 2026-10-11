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
import schema

st.set_page_config(page_title="HMDA Natural Language Explorer", page_icon="\U0001f3e0")

st.title("HMDA Natural Language Explorer")
st.caption("Ask a question in plain English about 2024 HMDA mortgage applications.")

with st.expander("About this dataset"):
    st.markdown(schema.GLOBAL_NOTES)

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
        st.success(result.answer or "Query succeeded, but no summary could be generated.")

        if result.rows:
            st.dataframe(pd.DataFrame(result.rows, columns=result.columns))
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
