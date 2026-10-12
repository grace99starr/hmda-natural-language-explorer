## Approach, Design Decisions, and Evaluation

### Approach

I decided to build a simple pipeline rather than a more complex AI agent. When a user asks a question, Claude Sonnet generates SQL using the HMDA dataset schema. The SQL is validated and executed in DuckDB, and the results are sent back to Claude to generate a plain English answer. The Streamlit interface displays the answer, underlying data, and generated SQL so users can verify the results.

I chose Claude Sonnet because it supports structured outputs and prompt caching and works well with detailed schema information. I also created a 'schema.py' file containing column definitions, categorical values, and known data quirks, which is used by both Claude and the SQL validator. For safety, I used SQLGlot to validate the structure of generated queries and disabled external access in DuckDB.

### Design Decisions

I considered using a ReAct-style agent that could explore the database and iteratively generate queries, but decided it would add unnecessary complexity for a single structured dataset. A fixed pipeline is easier to test, debug, and understand.

I chose DuckDB over SQLite because the workload is primarily analytical, involving aggregations, filtering, and grouping. I also chose AST-based SQL validation over keyword matching because it allows me to check the actual structure of a query rather than relying on potentially unreliable text patterns.

### Evaluation and Limitations

I evaluated the system on 20 questions covering basic queries, complex analytical queries, unanswerable questions, and ambiguous questions. I measured both response classification and numerical correctness against independently written reference SQL queries.

The pipeline correctly classified 19/20 questions (95%) and produced numerically correct results for 7/10 answerable questions (70%). All five basic queries were correct, while two of the five complex queries matched the reference results.

Most errors came from differences in how Claude interpreted analytical questions, such as applying filters that weren't explicitly requested or using different definitions when calculating denial rates. This showed me that the biggest area for improvement is handling questions where the correct calculation depends on assumptions that aren't clearly stated.

With more time, I would focus on defining common metrics more explicitly, asking for clarification when necessary, and expanding the evaluation of the plain English summaries. I also chose not to implement automatic retries, prioritizing predictable behavior and visible errors over attempting to recover from every failed query.