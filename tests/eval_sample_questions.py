"""
Ask generate_sql() a handful of sample questions and print the structured
response for each, as a quick manual check of status/sql/message behavior.

Run from the project root: .venv/bin/python tests/eval_sample_questions.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import llm

SAMPLE_QUESTIONS = [
    # Expected: success
    "What is the median loan amount for originated loans in Los Angeles County?",
    "How many applications were denied in 2024?",
    # Expected: unanswerable
    "What was the denial rate in Texas in 2023?",
    "What is the applicant's exact credit score?",
    # Expected: clarification_needed
    "What percentage of applications were approved?",
    "What's the average interest rate for approved loans?",
]


def run_sample_questions() -> None:
    """Ask a handful of sample questions and print the structured response for each."""
    for question in SAMPLE_QUESTIONS:
        result = llm.generate_sql(question)
        print(f"Question: {question}")
        print(f"  status:  {result.status}")
        print(f"  sql:     {result.sql}")
        print(f"  message: {result.message}")
        print("---")


if __name__ == "__main__":
    run_sample_questions()
