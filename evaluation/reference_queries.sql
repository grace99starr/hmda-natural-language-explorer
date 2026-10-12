-- Ground-truth SQL for the answerable cases in cases.json (ids 1-10).
-- Each block is labeled "-- id: N" so evaluate.py can parse it by id.
-- This SQL is developer-authored and trusted: evaluate.py runs it directly
-- against the DuckDB connection, bypassing validator.validate_sql() (which
-- only exists to sandbox untrusted LLM-generated SQL).

-- id: 1
-- How many mortgage applications are in the dataset?
SELECT COUNT(*) AS application_count FROM hmda

-- id: 2
-- What is the average loan amount by loan type?
SELECT loan_type, ROUND(AVG(loan_amount), 2) AS avg_loan_amount, COUNT(*) AS n
FROM hmda
GROUP BY loan_type
ORDER BY loan_type

-- id: 3
-- How many applications were denied in each county?
SELECT county_code, COUNT(*) AS denied_count
FROM hmda
WHERE action_taken = 3
GROUP BY county_code
ORDER BY county_code

-- id: 4
-- What is the median loan amount for originated loans in Los Angeles County?
SELECT MEDIAN(loan_amount) AS median_loan_amount
FROM hmda
WHERE action_taken = 1 AND county_code = 6037

-- id: 5
-- What are the five most common loan purposes?
SELECT loan_purpose, COUNT(*) AS n
FROM hmda
GROUP BY loan_purpose
ORDER BY n DESC
LIMIT 5

-- id: 6
-- What percentage of applications were denied in each county?
-- Excludes action_taken = 6 (purchased loan) from the denominator -- a
-- purchase isn't a new underwriting decision, so it shouldn't dilute the
-- denial rate (per schema.py's note on action_taken = 6).
SELECT
    county_code,
    ROUND(100.0 * SUM(CASE WHEN action_taken = 3 THEN 1 ELSE 0 END) / COUNT(*), 2) AS denial_rate_pct
FROM hmda
WHERE action_taken != 6
GROUP BY county_code
ORDER BY county_code

-- id: 7
-- What were the three most common denial reasons for denied applications?
-- Unions denial_reason-1..4 rather than just the first column, per schema.py's
-- documented convention (up to 4 reasons can be recorded per denied application).
-- 10 (not applicable) and 1111 (exempt) are excluded as non-reasons.
WITH denial_reasons AS (
    SELECT "denial_reason-1" AS reason FROM hmda WHERE action_taken = 3
    UNION ALL
    SELECT "denial_reason-2" AS reason FROM hmda WHERE action_taken = 3
    UNION ALL
    SELECT "denial_reason-3" AS reason FROM hmda WHERE action_taken = 3
    UNION ALL
    SELECT "denial_reason-4" AS reason FROM hmda WHERE action_taken = 3
)
SELECT reason, COUNT(*) AS n
FROM denial_reasons
WHERE reason IS NOT NULL AND reason NOT IN (10, 1111)
GROUP BY reason
ORDER BY n DESC
LIMIT 3

-- id: 8
-- What is the average interest rate by loan type for originated loans?
-- interest_rate is stored as VARCHAR (mixes numeric strings with "Exempt"/blank),
-- so it must be TRY_CAST before aggregating, per schema.py's note.
SELECT loan_type, ROUND(AVG(TRY_CAST(interest_rate AS DOUBLE)), 3) AS avg_interest_rate, COUNT(*) AS n
FROM hmda
WHERE action_taken = 1 AND TRY_CAST(interest_rate AS DOUBLE) IS NOT NULL
GROUP BY loan_type
ORDER BY loan_type

-- id: 9
-- How does the average loan amount compare between Los Angeles and Santa Barbara counties?
SELECT county_code, ROUND(AVG(loan_amount), 2) AS avg_loan_amount, COUNT(*) AS n
FROM hmda
WHERE county_code IN (6037, 6083)
GROUP BY county_code
ORDER BY county_code

-- id: 10
-- Which loan type had the highest denial rate?
-- Same action_taken != 6 exclusion as id 6, applied per loan_type instead of county.
SELECT
    loan_type,
    ROUND(100.0 * SUM(CASE WHEN action_taken = 3 THEN 1 ELSE 0 END) / COUNT(*), 2) AS denial_rate_pct
FROM hmda
WHERE action_taken != 6
GROUP BY loan_type
ORDER BY denial_rate_pct DESC
LIMIT 1
