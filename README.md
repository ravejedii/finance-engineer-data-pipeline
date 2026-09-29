# Kiln: settlement-to-ledger

An analytics engineering case study: ingest payment-processor settlement
reports into a double-entry ledger, encode accounting policy (revenue,
FX, bad debt) in dbt, and reconcile reported numbers back to source.

**All data in this repository is synthetic.** Kiln is a fictional
creator-commerce platform, and both processors are fictional. No real
company, person, or transaction appears anywhere.

> Status: Phase 0 (setup). The full case study write-up comes in Phase 8.

## Stack

Python 3.11 + uv, dbt-core + dbt-bigquery, sqlfluff (jinja templater),
pre-commit, GitHub Actions. Design choices and their tradeoffs are in
[DECISIONS.md](DECISIONS.md).

## Layout

```
generator/   seeded synthetic data generator (Phase 1)
loader/      idempotent raw-file loader (Phase 2)
transform/   dbt project: staging -> intermediate -> marts
tests/       Python tests
```

## Running it

```bash
uv sync
uv run pre-commit install

export GCP_PROJECT_ID=<your-project>
export GOOGLE_APPLICATION_CREDENTIALS=<path-to-service-account-keyfile>  # or: gcloud auth application-default login
export DBT_DATASET=kiln_dev_<yourname>   # optional; default kiln_dev

cd transform
uv run dbt debug --profiles-dir .
uv run dbt build --profiles-dir .
```

### BigQuery setup (one time)

1. A GCP project **with billing enabled** (the sandbox can't run the
   `merge` statements that incremental models need; see DECISIONS.md).
2. A budget with an email alert, plus a custom quota on "Query usage per
   day". The budget alert only notifies; the quota and the profile's
   `maximum_bytes_billed` are what actually stop spend.
3. A service account with `BigQuery Data Editor` and `BigQuery Job User`
   on the project, and a JSON key.
4. In GitHub: repository secret `GCP_SA_KEY` (the key JSON) and repository
   variable `GCP_PROJECT_ID`. Optional variable `BQ_LOCATION` (default `US`).

CI runs each PR against its own `ci_pr_<n>` datasets and drops them at
the end of the run.
