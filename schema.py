"""
HMDA LAR schema description for LLM-generated SQL.

Built from the CSV header of data/hmda_2024.csv (99 columns) cross-referenced
against the FFIEC LAR data field documentation:
https://ffiec.cfpb.gov/documentation/publications/loan-level-datasets/lar-data-fields

Purpose: render a compact, accurate data dictionary (column name, SQL type,
meaning, and coded values) to splice into the system prompt of whatever LLM
generates SQL against this table. Keeping it in one module means the NL->SQL
layer and any notebook/eval code share the exact same source of truth.

Querying convention assumed below: the data is loaded into DuckDB as a table
or view named `hmda` (e.g. `duckdb.sql("CREATE VIEW hmda AS SELECT * FROM df")`).
Many column names contain hyphens (e.g. "denial_reason-1", "derived_msa-md"),
so they MUST be double-quoted in generated SQL: SELECT "denial_reason-1" ...
"""

from dataclasses import dataclass, field

import duckdb


@dataclass
class Column:
    name: str
    sql_type: str
    category: str
    description: str
    values: dict | None = None
    notes: str | None = None


# Shared value maps reused across repeated columns (ethnicity-2..5, race-2..5,
# aus-2..5, denial_reason-2..4, co-applicant_* mirrors of applicant_*).
ETHNICITY_CODES = {
    1: "Hispanic or Latino",
    11: "Mexican",
    12: "Puerto Rican",
    13: "Cuban",
    14: "Other Hispanic or Latino",
    2: "Not Hispanic or Latino",
}
ETHNICITY_PRIMARY_EXTRA = {3: "Information not provided", 4: "Not applicable"}
CO_ETHNICITY_PRIMARY_EXTRA = {3: "Information not provided", 4: "Not applicable", 5: "No co-applicant"}

RACE_CODES = {
    1: "American Indian or Alaska Native",
    2: "Asian",
    21: "Asian Indian",
    22: "Chinese",
    23: "Filipino",
    24: "Japanese",
    25: "Korean",
    26: "Vietnamese",
    27: "Other Asian",
    3: "Black or African American",
    4: "Native Hawaiian or Other Pacific Islander",
    41: "Native Hawaiian",
    42: "Guamanian or Chamorro",
    43: "Samoan",
    44: "Other Pacific Islander",
    5: "White",
}
RACE_PRIMARY_EXTRA = {6: "Information not provided", 7: "Not applicable"}
CO_RACE_PRIMARY_EXTRA = {6: "Information not provided", 7: "Not applicable", 8: "No co-applicant"}

OBSERVED_CODES = {1: "Collected on visual observation/surname", 2: "Not collected on visual observation/surname", 3: "Not applicable"}
CO_OBSERVED_CODES = {**OBSERVED_CODES, 4: "No co-applicant"}

AUS_CODES = {
    1: "Desktop Underwriter (DU)",
    2: "Loan Prospector/Loan Product Advisor",
    3: "Technology Open To Approved Lenders (TOTAL) Scorecard",
    4: "Guaranteed Underwriting System (GUS)",
    7: "Internal proprietary system",
    8: "Other",
}
AUS1_EXTRA = {5: "Not applicable", 6: "NA / exempt"}

DENIAL_REASON_CODES = {
    1: "Debt-to-income ratio",
    2: "Employment history",
    3: "Credit history",
    4: "Collateral",
    5: "Insufficient cash (downpayment, closing costs)",
    6: "Unverifiable information",
    7: "Credit application incomplete",
    8: "Mortgage insurance denied",
    9: "Other",
}
DENIAL_REASON1_EXTRA = {10: "Not applicable (not a denied application)"}

AGE_BRACKETS_NOTE = "Binned string: '<25','25-34','35-44','45-54','55-64','65-74','>74'. 8888 = not applicable (applicant/co-applicant is not a natural person, e.g. a business entity; can appear in applicant_age too, not just co-applicant_age). 9999 = no co-applicant (co-applicant_age only)."

YES_NO_EXEMPT = {1: "Yes", 2: "No", 1111: "Exempt"}


COLUMNS: list[Column] = [
    # ---- Identifiers / geography ----
    Column("activity_year", "BIGINT", "geo", "Calendar year of the loan activity. Constant 2024 in this extract."),
    Column("lei", "VARCHAR", "geo", "Legal Entity Identifier of the reporting lender institution."),
    Column("derived_msa-md", "BIGINT", "geo", "Metropolitan Statistical Area/Metropolitan Division code of the property."),
    Column("state_code", "VARCHAR", "geo", "Two-letter US state abbreviation. Always 'CA' in this extract.", notes="~61 rows have a null state_code despite being in-scope; treat as missing, not a filter error."),
    Column("county_code", "BIGINT", "geo", "FIPS county code.", values={6037: "Los Angeles County", 6083: "Santa Barbara County"}),
    Column("census_tract", "DOUBLE", "geo", "11-digit census tract FIPS code identifying the property location."),

    # ---- Loan classification ----
    Column("conforming_loan_limit", "VARCHAR", "classification", "Whether the loan amount is at/below the conforming limit for its area.", values={"C": "Conforming", "NC": "Nonconforming", "U": "Undetermined", "NA": "Not applicable"}),
    Column("derived_loan_product_type", "VARCHAR", "classification", "Derived combination of loan_type and lien_status.", values={
        "Conventional:First Lien": "", "Conventional:Subordinate Lien": "", "FHA:First Lien": "",
        "FHA:Subordinate Lien": "", "VA:First Lien": "", "VA:Subordinate Lien": "",
        "FSA/RHS:First Lien": "", "FSA/RHS:Subordinate Lien": "",
    }),
    Column("derived_dwelling_category", "VARCHAR", "classification", "Derived combination of construction_method and property unit count.", values={
        "Single Family (1-4 Units):Site-Built": "", "Single Family (1-4 Units):Manufactured": "",
        "Multifamily:Site-Built (5+ Units)": "", "Multifamily:Manufactured (5+ Units)": "",
    }),
    Column("derived_ethnicity", "VARCHAR", "classification", "Derived single-value ethnicity summarizing applicant + co-applicant ethnicity fields.", values={
        "Hispanic or Latino": "", "Not Hispanic or Latino": "", "Joint": "applicant and co-applicant reported different ethnicities",
        "Ethnicity Not Available": "", "Free Form Text Only": "",
    }, notes="Prefer this over the raw applicant_ethnicity-N columns for simple ethnicity breakdowns; use the raw columns only when the question needs applicant-vs-co-applicant detail or Hispanic subgroup (Mexican/Puerto Rican/Cuban)."),
    Column("derived_race", "VARCHAR", "classification", "Derived single-value race summarizing applicant + co-applicant race fields.", values={
        "American Indian or Alaska Native": "", "Asian": "", "Black or African American": "",
        "Native Hawaiian or Other Pacific Islander": "", "White": "", "2 or more minority races": "",
        "Joint": "applicant and co-applicant reported different races", "Free Form Text Only": "", "Race Not Available": "",
    }, notes="Prefer this over the raw applicant_race-N columns for simple race breakdowns."),
    Column("derived_sex", "VARCHAR", "classification", "Derived single-value sex summarizing applicant + co-applicant sex fields.", values={"Male": "", "Female": "", "Joint": "", "Sex Not Available": ""}),
    Column("action_taken", "BIGINT", "classification", "Outcome of the application. THE key field for approval/denial analysis.", values={
        1: "Loan originated", 2: "Application approved but not accepted", 3: "Application denied",
        4: "Application withdrawn by applicant", 5: "File closed for incompleteness",
        6: "Purchased loan (institution bought a loan originated elsewhere)",
        7: "Preapproval request denied", 8: "Preapproval request approved but not accepted",
    }, notes="'Denied' questions should filter action_taken = 3, not just low-code values. 'Approved'/'originated' questions usually mean action_taken = 1. Code 6 (purchased) is not a new underwriting decision -- exclude it from denial/approval rate questions unless asked about purchased loans specifically."),
    Column("purchaser_type", "BIGINT", "classification", "Entity that purchased the loan after origination, if any.", values={
        0: "Not applicable (not sold)", 1: "Fannie Mae", 2: "Ginnie Mae", 3: "Freddie Mac", 4: "Farmer Mac",
        5: "Private securitizer", 6: "Commercial bank/savings bank/savings association", 71: "Credit union/mortgage company/finance company",
        72: "Life insurance company", 8: "Affiliate institution", 9: "Other",
    }),
    Column("preapproval", "BIGINT", "classification", "Whether a preapproval was requested.", values={1: "Preapproval requested", 2: "Preapproval not requested"}),
    Column("loan_type", "BIGINT", "classification", "Type of loan guarantee/insurance.", values={1: "Conventional (not insured or guaranteed)", 2: "FHA insured", 3: "VA guaranteed", 4: "USDA RHS/FSA guaranteed"}),
    Column("loan_purpose", "BIGINT", "classification", "Stated purpose of the loan.", values={1: "Home purchase", 2: "Home improvement", 31: "Refinancing", 32: "Cash-out refinancing", 4: "Other purpose", 5: "Not applicable"}),
    Column("lien_status", "BIGINT", "classification", "Lien position of the loan.", values={1: "First lien", 2: "Subordinate lien"}),
    Column("reverse_mortgage", "BIGINT", "classification", "Whether the loan is a reverse mortgage.", values=YES_NO_EXEMPT),
    Column("open-end_line_of_credit", "BIGINT", "classification", "Whether the loan is an open-end line of credit.", values=YES_NO_EXEMPT),
    Column("business_or_commercial_purpose", "BIGINT", "classification", "Whether the loan is primarily for a business or commercial purpose.", values=YES_NO_EXEMPT, notes="Large multifamily/commercial loans (e.g. loan_amount in the hundreds of millions) typically have this = 1. Exclude these when a question implies ordinary residential borrowers (e.g. 'average loan amount for homebuyers') to avoid skewing the mean; use median or filter business_or_commercial_purpose = 2 in that case."),

    # ---- Loan amount / pricing ----
    Column("loan_amount", "DOUBLE", "pricing", "Loan amount in dollars, rounded per HMDA disclosure rules.", notes="Heavily right-skewed: a handful of multifamily/commercial loans exceed $100M while the median loan is ~$425K. Prefer median over mean for 'typical' loan amount questions, or report both."),
    Column("loan_to_value_ratio", "VARCHAR", "pricing", "Combined loan-to-value ratio as a percentage of property value.", notes="Stored as text; cast with TRY_CAST(... AS DOUBLE) before arithmetic -- some rows are 'NA' or 'Exempt'."),
    Column("interest_rate", "VARCHAR", "pricing", "Note interest rate, percent.", notes="Stored as text, not numeric. Values like 'Exempt' and blank/NA are mixed in with numeric strings, and the same rate appears with inconsistent trailing zeros ('6.5' vs '6.500'). Always TRY_CAST(... AS DOUBLE) and filter out NULLs/non-numeric before AVG/stats."),
    Column("rate_spread", "VARCHAR", "pricing", "Rate spread between the APR and the average prime offer rate.", notes="Text column, same casting caveat as interest_rate; mostly NA for loans not subject to Reg Z rate-spread reporting."),
    Column("hoepa_status", "BIGINT", "pricing", "Whether the loan is subject to HOEPA (high-cost mortgage) protections.", values={1: "High-cost mortgage", 2: "Not a high-cost mortgage", 3: "Not applicable"}),
    Column("total_loan_costs", "VARCHAR", "pricing", "Total closing costs charged, dollars.", notes="Text column with NA values; TRY_CAST before aggregation."),
    Column("total_points_and_fees", "VARCHAR", "pricing", "Total points and fees charged, dollars.", notes="Text column with NA values; TRY_CAST before aggregation."),
    Column("origination_charges", "VARCHAR", "pricing", "Origination charges, dollars.", notes="Text column with NA values; TRY_CAST before aggregation."),
    Column("discount_points", "VARCHAR", "pricing", "Discount points paid, dollars.", notes="Text column with NA values; TRY_CAST before aggregation."),
    Column("lender_credits", "VARCHAR", "pricing", "Lender credits applied, dollars.", notes="Text column with NA values; TRY_CAST before aggregation."),
    Column("loan_term", "VARCHAR", "pricing", "Loan term in months.", notes="Mostly numeric strings (e.g. '360'); TRY_CAST(... AS INTEGER) before aggregation."),
    Column("prepayment_penalty_term", "VARCHAR", "pricing", "Prepayment penalty term in months, if any.", notes="Mostly NA; text column."),
    Column("intro_rate_period", "VARCHAR", "pricing", "Introductory/teaser rate period in months, if any.", notes="Mostly NA; text column."),

    # ---- Loan features (non-amortizing risk flags) ----
    Column("negative_amortization", "BIGINT", "features", "Whether the loan has a negative amortization feature.", values=YES_NO_EXEMPT),
    Column("interest_only_payment", "BIGINT", "features", "Whether the loan allows interest-only payments.", values=YES_NO_EXEMPT),
    Column("balloon_payment", "BIGINT", "features", "Whether the loan has a balloon payment feature.", values=YES_NO_EXEMPT),
    Column("other_nonamortizing_features", "BIGINT", "features", "Whether the loan has other non-fully-amortizing features.", values=YES_NO_EXEMPT),

    # ---- Property ----
    Column("property_value", "VARCHAR", "property", "Value of the property, dollars.", notes="Text column with NA values; TRY_CAST before aggregation."),
    Column("construction_method", "BIGINT", "property", "Construction method of the dwelling.", values={1: "Site-built", 2: "Manufactured home"}),
    Column("occupancy_type", "BIGINT", "property", "Occupancy type of the property.", values={1: "Principal residence", 2: "Second residence", 3: "Investment property"}),
    Column("manufactured_home_secured_property_type", "BIGINT", "property", "For manufactured homes, whether land is also secured.", values={1: "Manufactured home and land", 2: "Manufactured home and not land", 3: "Not applicable", 1111: "Exempt"}),
    Column("manufactured_home_land_property_interest", "BIGINT", "property", "For manufactured homes, applicant's property interest in the land.", values={1: "Direct ownership", 2: "Indirect ownership", 3: "Paid leasehold", 4: "Unpaid leasehold", 5: "Not applicable", 1111: "Exempt"}),
    Column("total_units", "VARCHAR", "property", "Number of individual dwelling units related to the property.", values={"1": "", "2": "", "3": "", "4": "", "5-24": "", "25-49": "", "50-99": "", "100-149": "", ">149": ""}, notes="Categorical string, not numeric; large bucket values (e.g. '>149') indicate multifamily/commercial properties -- useful for excluding outlier loan_amount rows."),
    Column("multifamily_affordable_units", "VARCHAR", "property", "Number of units in a multifamily property considered affordable.", notes="Text column, mostly NA (only applies to multifamily properties)."),

    # ---- Applicant financials ----
    Column("income", "DOUBLE", "financial", "Gross annual income relied on in underwriting, in thousands of dollars.", notes="Units are thousands of USD (e.g. 160 = $160,000/year), not raw dollars. A small number of negative or extremely large values exist (reporting errors) -- consider filtering obvious outliers for aggregate stats."),
    Column("debt_to_income_ratio", "VARCHAR", "financial", "Debt-to-income ratio used in underwriting.", values={"<20%": "", "20%-<30%": "", "30%-<36%": "", "36": "exact value, 36%", "37": "", "38": "", "39": "", "40": "", "41": "", "42": "", "43": "", "44": "", "45": "", "46": "", "47": "", "48": "", "49": "", "50%-60%": "", ">60%": "", "Exempt": ""}, notes="Messy mixed-format column: values 36-49 are exact integer percentages reported as strings, while others are pre-binned range strings ('30%-<36%', '50%-60%', '>60%', '<20%'). There is no single numeric cast that works for all rows -- to analyze this, bucket both representations into the same set of ranges (e.g. map '42' -> '36%-49%' bucket) rather than averaging it as a number."),

    # ---- Credit score type (methodology used, not the score itself -- scores are not disclosed) ----
    Column("applicant_credit_score_type", "BIGINT", "credit", "Credit scoring model used for the applicant.", values={
        1: "Equifax Beacon 5.0", 2: "Experian Fair Isaac", 3: "FICO Risk Score Classic 04", 4: "FICO Risk Score Classic 98",
        5: "VantageScore 2.0", 6: "VantageScore 3.0", 7: "More than one scoring model", 8: "Other", 9: "Not applicable", 1111: "Exempt",
    }),
    Column("co-applicant_credit_score_type", "BIGINT", "credit", "Credit scoring model used for the co-applicant.", values={
        1: "Equifax Beacon 5.0", 2: "Experian Fair Isaac", 3: "FICO Risk Score Classic 04", 4: "FICO Risk Score Classic 98",
        5: "VantageScore 2.0", 6: "VantageScore 3.0", 7: "More than one scoring model", 8: "Other", 9: "Not applicable", 10: "No co-applicant", 1111: "Exempt",
    }),

    # ---- Ethnicity detail (applicant has up to 5 reported, co-applicant up to 5) ----
    Column("applicant_ethnicity-1", "DOUBLE", "ethnicity", "Applicant's primary reported ethnicity.", values={**ETHNICITY_CODES, **ETHNICITY_PRIMARY_EXTRA}, notes="143 rows are truly null (not even code 3/4) despite being a required field; filter or coalesce before grouping."),
    *[Column(f"applicant_ethnicity-{i}", "DOUBLE", "ethnicity", f"Applicant's ethnicity detail #{i} (additional selection beyond the primary).", values=ETHNICITY_CODES) for i in (2, 3, 4, 5)],
    Column("co-applicant_ethnicity-1", "DOUBLE", "ethnicity", "Co-applicant's primary reported ethnicity.", values={**ETHNICITY_CODES, **CO_ETHNICITY_PRIMARY_EXTRA}, notes="39 rows are truly null despite being a required field; filter or coalesce before grouping."),
    *[Column(f"co-applicant_ethnicity-{i}", "DOUBLE", "ethnicity", f"Co-applicant's ethnicity detail #{i}.", values=ETHNICITY_CODES) for i in (2, 3, 4, 5)],
    Column("applicant_ethnicity_observed", "BIGINT", "ethnicity", "Whether applicant ethnicity was collected via visual observation/surname rather than self-reported.", values=OBSERVED_CODES),
    Column("co-applicant_ethnicity_observed", "BIGINT", "ethnicity", "Whether co-applicant ethnicity was collected via visual observation/surname.", values=CO_OBSERVED_CODES),

    # ---- Race detail ----
    Column("applicant_race-1", "DOUBLE", "race", "Applicant's primary reported race.", values={**RACE_CODES, **RACE_PRIMARY_EXTRA}, notes="145 rows are truly null despite being a required field; filter or coalesce before grouping."),
    *[Column(f"applicant_race-{i}", "DOUBLE", "race", f"Applicant's race detail #{i}.", values=RACE_CODES) for i in (2, 3, 4, 5)],
    Column("co-applicant_race-1", "DOUBLE", "race", "Co-applicant's primary reported race.", values={**RACE_CODES, **CO_RACE_PRIMARY_EXTRA}, notes="55 rows are truly null despite being a required field; filter or coalesce before grouping."),
    *[Column(f"co-applicant_race-{i}", "DOUBLE", "race", f"Co-applicant's race detail #{i}.", values=RACE_CODES) for i in (2, 3, 4, 5)],
    Column("applicant_race_observed", "BIGINT", "race", "Whether applicant race was collected via visual observation/surname.", values=OBSERVED_CODES),
    Column("co-applicant_race_observed", "BIGINT", "race", "Whether co-applicant race was collected via visual observation/surname.", values=CO_OBSERVED_CODES),

    # ---- Sex / age ----
    Column("applicant_sex", "BIGINT", "sex_age", "Applicant's reported sex.", values={1: "Male", 2: "Female", 3: "Information not provided", 4: "Not applicable", 6: "Selected both male and female"}),
    Column("co-applicant_sex", "BIGINT", "sex_age", "Co-applicant's reported sex.", values={1: "Male", 2: "Female", 3: "Information not provided", 4: "Not applicable", 5: "No co-applicant", 6: "Selected both male and female"}),
    Column("applicant_sex_observed", "BIGINT", "sex_age", "Whether applicant sex was collected via visual observation/surname.", values=OBSERVED_CODES),
    Column("co-applicant_sex_observed", "BIGINT", "sex_age", "Whether co-applicant sex was collected via visual observation/surname.", values=CO_OBSERVED_CODES),
    Column("applicant_age", "VARCHAR", "sex_age", "Applicant's age bracket.", notes=AGE_BRACKETS_NOTE),
    Column("co-applicant_age", "VARCHAR", "sex_age", "Co-applicant's age bracket.", notes=AGE_BRACKETS_NOTE),
    Column("applicant_age_above_62", "VARCHAR", "sex_age", "Whether the applicant is above age 62.", values={"Yes": "", "No": "", "NA": "Not applicable/not available"}),
    Column("co-applicant_age_above_62", "VARCHAR", "sex_age", "Whether the co-applicant is above age 62.", values={"Yes": "", "No": "", "NA": "Not applicable/not available"}),

    # ---- Process / underwriting ----
    Column("submission_of_application", "BIGINT", "process", "Whether the application was submitted directly to the institution making the credit decision.", values={1: "Submitted directly", 2: "Not submitted directly", 3: "Not applicable", 1111: "Exempt"}),
    Column("initially_payable_to_institution", "BIGINT", "process", "Whether the obligation was initially payable to this institution.", values={1: "Initially payable to this institution", 2: "Not initially payable to this institution", 3: "Not applicable", 1111: "Exempt"}),
    Column("aus-1", "BIGINT", "process", "Primary automated underwriting system used.", values={**AUS_CODES, **AUS1_EXTRA}),
    *[Column(f"aus-{i}", "DOUBLE", "process", f"Additional automated underwriting system #{i}, if more than one was used.", values=AUS_CODES) for i in (2, 3, 4, 5)],

    # ---- Denial reasons ----
    Column("denial_reason-1", "BIGINT", "denial", "First reported reason for denial.", values={**DENIAL_REASON_CODES, **DENIAL_REASON1_EXTRA}, notes="For non-denied applications (action_taken != 3) this is usually 10 (not applicable). To rank 'main denial reasons', filter action_taken = 3 AND denial_reason-1 NOT IN (10, 1111), and consider UNIONing denial_reason-1..4 since up to 4 reasons can be recorded per denied application -- counting only column 1 undercounts reasons like 'Other' that often appear as a secondary reason."),
    *[Column(f"denial_reason-{i}", "DOUBLE", "denial", f"Additional denial reason #{i}, if more than one reason was given.", values=DENIAL_REASON_CODES) for i in (2, 3, 4)],

    # ---- Census tract context (ACS-derived, describes the neighborhood, not the applicant) ----
    Column("tract_population", "BIGINT", "tract", "Total population of the property's census tract."),
    Column("tract_minority_population_percent", "DOUBLE", "tract", "Percent of the tract population that is a minority race/ethnicity."),
    Column("ffiec_msa_md_median_family_income", "BIGINT", "tract", "FFIEC estimated median family income for the tract's MSA/MD, dollars."),
    Column("tract_to_msa_income_percentage", "BIGINT", "tract", "Tract's median family income as a percentage of the MSA/MD median family income."),
    Column("tract_owner_occupied_units", "BIGINT", "tract", "Number of owner-occupied housing units in the tract."),
    Column("tract_one_to_four_family_homes", "BIGINT", "tract", "Number of 1-4 family dwelling units in the tract."),
    Column("tract_median_age_of_housing_units", "BIGINT", "tract", "Median age of housing units in the tract, years."),
]


TABLE_NAME = "hmda"

GLOBAL_NOTES = """\
- Table name: {table}. Identifiers with hyphens (e.g. "denial_reason-1", "derived_msa-md",
  "co-applicant_sex") MUST be double-quoted in SQL.
- Scope: 2024 mortgage applications for Los Angeles County (06037) and Santa Barbara
  County (06083), CA only. There is no other year or geography in this table --
  a question about a different year/state/county is out of scope and should be
  rejected rather than silently answered.
- Several numeric-looking columns are stored as VARCHAR because they mix real
  values with sentinel strings ("NA", "Exempt") or are pre-binned ranges
  (loan_to_value_ratio, interest_rate, rate_spread, total_loan_costs,
  total_points_and_fees, origination_charges, discount_points, lender_credits,
  loan_term, property_value, debt_to_income_ratio). Use TRY_CAST(... AS DOUBLE)
  and exclude NULLs before aggregating; never assume a plain CAST will succeed.
- "Originated loans" means action_taken = 1.
- "Approved applications" may include action_taken IN (1, 2),
  depending on the question.
- "Denied applications" means action_taken = 3.
- Do not treat originated and approved-but-not-accepted
  applications as identical outcomes.
- For ambiguous approval-rate questions, clarify the
  definition or explicitly state which outcomes are included.
- Denial reasons can span up to 4 columns ("denial_reason-1".."denial_reason-4")
  per application; "main denial reasons" style questions should consider all
  four, not just the first.
- loan_amount is heavily right-skewed by a small number of large multifamily/
  commercial loans; prefer median, or exclude business_or_commercial_purpose = 1
  rows, when a question implies "typical" residential borrowers.
""".format(table=TABLE_NAME)


def render_schema_for_prompt() -> str:
    """Render the full column catalog as markdown for inclusion in an LLM system prompt."""
    lines = [f"## Table: {TABLE_NAME}\n", GLOBAL_NOTES, "\n## Columns\n"]
    for c in COLUMNS:
        line = f"- `{c.name}` ({c.sql_type}): {c.description}"
        if c.values:
            rendered_vals = ", ".join(
                f"{k}={v}" if v else f"{k}" for k, v in c.values.items()
            )
            line += f" Values: {rendered_vals}."
        if c.notes:
            line += f" NOTE: {c.notes}"
        lines.append(line)
    return "\n".join(lines)


def validate_schema(con: duckdb.DuckDBPyConnection | None = None) -> list[str]:
    """
    Compare COLUMNS against the live `{TABLE_NAME}` table/view visible to `con`
    (or the default duckdb connection if `con` is None). Returns a list of
    human-readable discrepancies; an empty list means schema.py matches the
    database. Catches both drift in column names and drift in column types.
    """
    engine = con if con is not None else duckdb
    try:
        actual = engine.sql(f"DESCRIBE SELECT * FROM {TABLE_NAME}").df()
    except duckdb.Error as e:
        return [f"Could not DESCRIBE '{TABLE_NAME}': {e}"]

    actual_types = dict(zip(actual["column_name"], actual["column_type"]))
    schema_names = {c.name for c in COLUMNS}
    actual_names = set(actual_types)

    errors = []
    for name in sorted(schema_names - actual_names):
        errors.append(f"'{name}' is documented in schema.py but does not exist in '{TABLE_NAME}'.")
    for name in sorted(actual_names - schema_names):
        errors.append(f"'{name}' exists in '{TABLE_NAME}' but is not documented in schema.py.")
    for c in COLUMNS:
        if c.name in actual_types and actual_types[c.name] != c.sql_type:
            errors.append(f"'{c.name}' is documented as {c.sql_type} but DuckDB reports {actual_types[c.name]}.")

    return errors


if __name__ == "__main__":
    import sys
    from pathlib import Path

    import pandas as pd

    print(render_schema_for_prompt())
    print("\n\n--- column count:", len(COLUMNS), "---")

    csv_path = Path(__file__).parent / "data" / "hmda_2024.csv"
    if not csv_path.exists():
        print(f"\nSkipping validation: {csv_path} not found.")
        sys.exit(0)

    df = pd.read_csv(csv_path, low_memory=False)
    duckdb.register(TABLE_NAME, df)
    errors = validate_schema()
    if errors:
        print(f"\n{len(errors)} schema/database mismatch(es):")
        for e in errors:
            print(" -", e)
        sys.exit(1)
    print(f"\nschema.py matches '{TABLE_NAME}': OK")
