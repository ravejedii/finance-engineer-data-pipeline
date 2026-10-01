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
