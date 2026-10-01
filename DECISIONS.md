# Decisions

Every non-trivial design choice, newest at the bottom. Format: decision,
alternatives considered, why, date.

---

## 001. Monorepo: Python at the root, dbt project in `transform/`

- **Decision:** One repo, one `pyproject.toml` and `uv.lock`. Python
  packages (`generator/`, `loader/`) at the root, dbt project in
  `transform/`.
- **Alternatives:** dbt project at the repo root (common for dbt-only
  repos); separate repos for ingestion and transformation.
- **Why:** Python is half this pipeline. Keeping dbt in its own directory
  makes clear what the dbt project contains, and a single lockfile means
  CI installs one environment. Separate repos would add cross-repo
  versioning for no benefit at this size.
- **Date:** 2026-09-29

## 002. BigQuery is the only warehouse, including CI

- **Decision:** dbt-bigquery for dev and CI. No DuckDB target.
- **Alternatives:** (a) DuckDB only, BigQuery added at the end (the
  original plan). (b) Two targets: DuckDB in CI, BigQuery for dev.
- **Why:** The target role's stack is dbt + BigQuery, and dialect-specific
  behavior (merge-based incrementals, partitioning and clustering,
  bytes-scanned cost, `safe_cast`, date functions) is exactly what's
  worth learning and defending. Option (b) means CI tests a dialect we
  don't ship and every model has to be written for two dialects. At CI
  scale the cost is close to zero (see 003).
- **Cost of this choice:** no offline development; CI needs cloud
  credentials; CI is slower than an in-process database; PRs from forks
  can't run the build job because secrets aren't exposed to them.
- **Date:** 2026-09-29

## 003. Billed GCP project with cost guardrails, not the BigQuery sandbox

- **Decision:** A billing-enabled project, with (1) a budget alert,
  (2) a custom "Query usage per day" quota, and (3) `maximum_bytes_billed`
  in every dbt profile target (5 GB dev, 1 GB CI).
- **Alternatives:** The BigQuery sandbox (no billing account needed).
- **Why:** Google's sandbox docs, checked 2026-09-29, list three
  deal-breakers: DML is unsupported, so no `merge`, which rules out
  incremental models and snapshots and makes idempotent loads harder;
  every table expires after 60 days; and the 10 GiB storage limit is
  *lifetime* and not refunded on deletion, which per-PR CI rebuilds
  would use up. Free-tier compute is the same either way (1 TiB of
  queries per month), so at this data size the billed project should
  cost about nothing.
- **Guardrail detail:** A budget alert only notifies. It never stops
  spend. The quota and `maximum_bytes_billed` are the controls that
  actually fail a query.
- **Date:** 2026-09-29

## 004. sqlfluff uses the jinja templater, not the dbt templater

- **Decision:** `templater = jinja` with `apply_dbt_builtins = True` and
  project macros loaded from `transform/macros`.
- **Alternatives:** The dbt templater, which compiles each file through
  dbt itself.
- **Why:** The dbt templater needs a working adapter connection, and with
  BigQuery that means cloud credentials in the lint job and on every
  pre-commit run. The jinja templater lints the same dbt SQL offline in
  about a second.
- **Cost of this choice:** `ref()`, `source()`, and `var()` render as
  stand-ins, so the linter never sees the real compiled SQL. It can miss
  problems that only show after compilation, and a complex macro may
  need a stand-in. `dbt build` in CI is the check on compiled SQL.
- **Note:** This choice is about *linting*. The dbt SQL we write is the
  same under either templater.
- **Date:** 2026-09-29

## 005. CI isolation: one dataset family per PR, dropped after every run

- **Decision:** CI builds into `ci_pr_<n>` (plus `ci_pr_<n>_staging` and
  so on). A `drop_ci_datasets` run-operation deletes them at the end of
  every run, including failed runs. Concurrency is one run per PR.
- **Alternatives:** A single shared `ci` dataset (PRs overwrite each
  other); datasets with a default table expiration instead of explicit
  cleanup.
- **Why:** PRs can't collide, and nothing lingers to cost storage. The
  macro matches `<dataset>_` exactly, so dropping `ci_pr_1` can't touch
  `ci_pr_12`, and it refuses to run on any target other than `ci`.
- **Date:** 2026-09-29

## 006. CI authenticates with a service-account key (for now) — superseded by 008

- **Decision:** JSON key stored as the `GCP_SA_KEY` GitHub secret, with
  the least-privilege roles `BigQuery Data Editor` and `BigQuery Job User`.
- **Alternatives:** Workload Identity Federation (GitHub OIDC token
  exchanged for short-lived GCP credentials, with no stored key).
- **Why:** A key takes minutes to set up. Workload Identity Federation is
  the better practice (nothing long-lived to leak) and is what I'd use
  on a real team. Revisit in Phase 6.
- **Date:** 2026-09-29

## 007. Tool versions locked through uv; pre-commit SQL hooks run via `uv run`

- **Decision:** `uv.lock` pins every version. dbt-core >= 1.10, which
  includes native unit tests (added in 1.8). The sqlfluff pre-commit
  hooks are local hooks that call `uv run sqlfluff`.
- **Alternatives:** The upstream sqlfluff pre-commit hook, which installs
  its own copy.
- **Why:** With `uv run`, the laptop, pre-commit, and CI all run the same
  sqlfluff version, so a rule can't pass locally and fail in CI.
- **Date:** 2026-09-29

## 008. CI authenticates through Workload Identity Federation (supersedes 006)

- **Decision:** GitHub Actions exchanges its OIDC token for short-lived
  GCP credentials and impersonates `kiln-ci`, a service account with only
  `BigQuery Data Editor` and `BigQuery Job User`. The provider's attribute
  condition accepts tokens from this one repository only. No key exists,
  and the workflow holds no secrets.
- **Alternatives:** (a) A service-account JSON key (decision 006).
  (b) Turning off the org policy that blocks key creation.
  (c) Direct WIF, which grants BigQuery roles to the GitHub identity
  itself with no service account in between.
- **Why:** The GCP organization enforces
  `iam.disableServiceAccountKeyCreation` (Google's secure-by-default
  setting for new organizations), so (a) was blocked. Turning the policy
  off to work around it is the wrong direction. WIF has no long-lived
  credential to leak, which matters because the repo will be public.
  (c) works, but not every Google API supports it yet; service-account
  impersonation is the most widely supported path.
- **Public-repo safety:** Two separate protections. (1) The attribute
  condition rejects tokens minted by *other* repositories. (2) It does
  **not** stop PRs from forks, because those run in this repo's context
  and carry this repo's name. What stops them is GitHub itself: under
  `pull_request` it caps a fork's workflow permissions at read-only, so
  it can't get an OIDC token. Hence the rule: never trigger this workflow
  on `pull_request_target`, which runs fork code with the base repo's
  permissions.
- **Cost of this choice:** More one-time setup (a pool, a provider, an IAM
  binding). Credentials exist only inside GitHub Actions, so a
  development machine needs its own auth (for example
  `gcloud auth application-default login`).
- **Date:** 2026-09-30

## 009. Generator simulates the truth first, then renders reports from it

- **Decision:** A day-by-day simulation of what actually happened at Kiln
  produces the truth. Each system's files are rendered from that truth, and
  the mess is applied afterward, with every affected row logged.
- **Alternatives:** Generating report rows directly, per file, with random
  values.
- **Why:** Tests need a correct answer. When the generator knows it made
  exactly 794 duplicates with these IDs, Phase 3 can prove it removed
  exactly those, and Phase 5 can prove each reconciliation break is real.
  Generating rows directly gives refunds with no matching charge and
  balances that never add up, so nothing could be reconciled to a known
  answer.
- **Date:** 2026-09-30

## 010. Standard library only for the generator

- **Decision:** `random`, `decimal`, `csv`, `zoneinfo`. No pandas, numpy,
  or Faker.
- **Why:** 350k orders take about 25 s in plain Python. That's fast enough,
  and it keeps money in `Decimal` and integers, never floats in a DataFrame.
  Each component gets its own seeded random stream derived from
  (seed, name), so adding a random draw in one place never shifts another
  component's output.
- **Date:** 2026-09-30

## 011. Raw formats are deliberately different per source

- **Decision:** Processor reports print decimal major units (`12.34`,
  `3736` for JPY). The app DB stores integer minor units. Processor B
  reports local CET/CEST time with a `TimeZone` column. FX is quoted as
  units per USD.
- **Why:** This is what real sources look like, and normalizing it
  (currency-aware minor-unit conversion, timezone conversion that handles
  DST, rate inversion) is the job of the staging layer. Pre-cleaned data
  would leave nothing to learn.
- **Date:** 2026-09-30

## 012. Rare events are dialed up at small scale

- **Decision:** The small scale raises the dispute rate (2% vs 0.5%), the
  processor A payout failure rate (3% vs 1%), and seller churn (90-day vs
  320-day mean lifetime).
- **Why:** Four months of CI data at realistic rates might contain zero
  failed payouts or negative balances, and then the tests would prove
  nothing. A test (`test_small_scale_exercises_every_issue`) fails if any
  issue count drops to zero.
- **Cost of this choice:** Small-scale business metrics aren't realistic.
  Analysis uses full scale only.
- **Date:** 2026-09-30

## 013. Scenario overlay kept separate from the baseline model

- **Decision:** Each stochastic stage of the simulation passes its
  parameters through `generator/scenarios.py`, which may adjust them.
- **Why:** The baseline model reads cleanly without it. What the overlay
  contains is intentionally undocumented until Phase 7.
- **Date:** 2026-09-30

## 014. Known simplification: processor A payout IDs are filled in retroactively

- **Decision:** A charge's `automatic_payout_id` is filled in even in the
  daily file for the day it was created, although the payout that sweeps it
  happens two days later.
- **Why:** It keeps payout-to-transaction matching possible from the files
  alone. A real itemized report run daily would leave the field blank
  until the payout happens.
- **Date:** 2026-09-30

## 015. The loader lands files verbatim; the file is the unit of idempotency

- **Decision:** Every raw column is `STRING`, exactly as delivered, plus
  lineage columns (`_source_file`, `_source_line`, `_load_id`, `_loaded_at`).
  Each file is fingerprinted with SHA-256 and recorded in `load_manifest`:
  - same name and same checksum: skipped;
  - same name and a new checksum: that file's rows are replaced as a unit.
- **Alternatives:** (a) `merge` on business keys at load time. (b) Typing
  columns in the loader. (c) Append-only raw with dedupe downstream.
- **Why:** (a) would silently remove the duplicates and restated rows that
  Phase 3 must prove it handles; raw should keep the mess. (b) means one
  `N/A` fails a whole file, and the loader starts holding business rules.
  (c) grows forever when a file is re-sent, and every consumer has to know
  which copy is current. Replace-by-file keeps raw equal to "the latest
  version of every file we received", and that can be verified
  (`loader verify`).
- **Naming note:** The lineage column is `_source_file`, not `_file_name`,
  because BigQuery reserves column names starting with `_FILE_` (as well as
  `_PARTITION`, `_TABLE_` and a few others). The first CI run caught this,
  and a unit test now guards every schema against those prefixes.
- **Date:** 2026-09-30

## 016. Two kinds of bad rows, caught in two places

- **Decision:** The loader rejects only what it can't split: a line with
  the wrong number of fields goes to `load_exceptions` with the line number,
  reason and raw text, and a file whose header drifts is rejected whole.
  Bad *values* (`N/A`, decimal commas, empty IDs) land in raw and are
  routed to exceptions by staging in Phase 3.
- **Why:** Structure is the loader's contract; meaning is staging's. Raw
  stays replayable: if a staging rule was wrong, fix it and rebuild,
  without re-fetching files.
- **Date:** 2026-09-30

## 017. BigQuery writes: free load jobs into scratch tables, then one transaction

- **Decision:** For each source, the new files' rows, exceptions and
  manifest entries go to scratch tables through load jobs. Then one
  multi-statement transaction deletes those files' old rows, inserts the
  new ones, and appends to the manifest. Scratch tables are dropped
  afterwards.
- **Alternatives:** Streaming inserts; a transaction per file; loading
  straight into the target table.
- **Why:** Load jobs cost nothing, and streaming inserts are billed and
  can't be deleted by DML right away (the streaming buffer). One
  transaction per source makes the delete-and-insert all-or-nothing, so a
  crash can't leave a file half-replaced. Batching by source rather than by
  file matters because a full-scale load is about 2,400 files, and a few
  seconds per transaction times 2,400 would take hours.
- **Date:** 2026-09-30

## 018. Freshness is measured on load time, not business time

- **Decision:** `dbt source freshness` checks `_loaded_at`, warning at 26
  hours and erroring at 50 hours.
- **Why:** The question freshness answers is "did the pipeline run?"
  Business timestamps in synthetic data are frozen in 2025 and would always
  fail. On a real platform I'd add a second check on business time per
  source (for example, the newest processor A `created_utc` is less than a
  day old) to catch a feed that loads on schedule but delivers stale files.
- **Date:** 2026-09-30

## 019. Backfill covers dated sources only

- **Decision:** `loader load --start --end` selects files by their business
  date. The date comes from the filename, or for processor B from the
  batch's rows, since its files are named by batch number. App DB extracts
  are undated full snapshots and load only in an unranged run.
- **Why:** A range should mean "the days in this range", so re-running a
  week can't silently reload today's app DB snapshot. A test proves a
  one-shot range load equals loading the same days one at a time.
- **Date:** 2026-09-30

## 031. Loader: free appends for new files, transactions only for replacements; every wait bounded

- **Decision:** Before writing a source, the loader checks whether any of its
  files already have rows. If none do (every first load, so every CI run),
  rows, exceptions and manifest are appended with free load jobs, manifest
  last. Only re-sent files go through the delete-and-insert transaction.
  Every BigQuery wait has a timeout (300 s per job, 60 s per API call), and
  the loader prints one progress line per source.
- **Why:** Two CI runs hung in the load step for 20 minutes with no output.
  Using DML transactions for files that had nothing to delete was the
  slowest, most failure-prone part of loading. Writing the manifest last
  keeps a crashed run safe: its rows have no manifest entry, so the next run
  sees the files as present and replaces them atomically.
- **Date:** 2026-10-01
