# Kiln dashboards (Metabase OSS)

Free, self-hosted Metabase on your machine. BigQuery is the warehouse; a local
Postgres container stores only Metabase's own dashboards and settings.

| Dashboard | Questions |
|---|---|
| Kiln: Finance close | trial balance check, income statement, three-way recon status, payout recon, order-to-settlement matching, missing processor B batches, realized and unrealized FX, bad-debt allowance |
| Kiln: Unit economics | GMV and net revenue, take rate and contribution margin, margin by region, economics by processor and plan, refund and dispute rates |
| Kiln: Churn and retention | seller and revenue churn rates, seller flows (new, returning, churned), churn vs account closures, churn by plan, LTV to date, seller retention and NRR by cohort |

Every question is native BigQuery SQL over the `kiln_marts` dataset, defined
in `dashboards.py`, so the dashboards are code-reviewed and reproducible.

## Run it

1. **Full-scale data in BigQuery.** Actions → `full-build` → Run workflow.
   It builds the persistent `kiln_*` datasets (about 353k orders).
2. **Start Metabase.**
   ```
   cp metabase/.env.example metabase/.env   # set MB_DB_PASS
   docker compose -f metabase/docker-compose.yml --env-file metabase/.env up -d
   ```
3. **A BigQuery credential for Metabase.** Metabase's BigQuery driver accepts
   only a service-account JSON key, and this project's org policy blocks key
   creation. See DECISIONS 045 for the options; the key belongs outside the
   repo at `~/.kiln/metabase-reader.json`.
4. **Create the connection and dashboards.**
   ```
   uv run python -m metabase.setup
   ```
   It asks for an admin email and password (first run creates the admin).
   Re-running replaces the Kiln dashboards rather than duplicating them.

Metabase listens on `127.0.0.1:3000` only.
