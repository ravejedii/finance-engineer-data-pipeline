# Kiln metrics

Every metric is defined once, in `transform/models/marts/metrics/_metrics.yml`
(dbt semantic layer). This page is the human-readable version. Dashboards read
the materialized marts (`mrt_*`), which compute the same definitions in SQL,
because Metabase OSS cannot query the dbt semantic layer directly.

All money is USD cents (integer). Ratios are computed from summed numerators
and denominators, never by averaging ratios.

**Kind** separates two kinds of number:

- **Accounting** numbers tie to a ledger account and the trial balance. The
  controller owns them. They change only with a journal entry.
- **Product** metrics are operating views of the business: volume, mix, rates.
  Strategic finance owns them. They may count things the ledger does not
  (orders, sellers).

| Metric | Definition | Grain | Kind | Owner | Ledger tie-out |
|---|---|---|---|---|---|
| GMV | Order amount at the booking-date FX rate, before refunds | order | product | strategic finance | none: Kiln is an agent, so GMV is not revenue |
| Net revenue | Platform fee revenue | order | accounting | accounting | account 4000 (credit) |
| Processing cost | Processor fees on charges | order | accounting | accounting | account 5000 |
| Contribution | Net revenue − processing cost − realized FX loss | order | product | strategic finance | 4000 − 5000 − 5200 |
| Take rate | Net revenue / GMV | order | product | strategic finance | |
| Contribution margin | Contribution / net revenue | order | product | strategic finance | |
| Orders | Count of orders | order | product | strategic finance | |
| Refund rate | Orders with at least one refund / orders | order | product | strategic finance | |
| Disputes | Disputes opened | order | product | risk | |
| Dispute rate (count) | Disputes / orders | order | product | risk | |
| Dispute loss | Disputed amounts − won reversals + dispute fees | order | accounting | risk | account 2000 lines for dispute events |
| Chargeback loss rate | Dispute loss / GMV | order | product | risk | |
| Active sellers | Sellers with at least one order in the period | seller × period | product | strategic finance | |
| Seller retention | Cohort sellers active in month *n* / cohort size | cohort × month | product | strategic finance | |
| Net revenue retention | Cohort net revenue in month *n* / cohort net revenue in month 0 | cohort × month | product | strategic finance | |

## Definitions that need a decision, and the one taken

- **Revenue is the fee, not the order.** Kiln arranges the sale and never
  controls the goods, so under ASC 606 it is an agent and books net. GMV is a
  volume metric only. See DECISIONS.md.
- **Refunds and disputes hit the seller's balance, not Kiln's P&L.** They reach
  Kiln's income statement only when a negative seller balance becomes bad debt
  (account 5300). That is why dispute loss is shown as a risk metric, not a
  cost line in contribution.
- **Orders are dated by order date**, not settlement date. A settlement-dated
  view belongs to accounting (the general ledger), not to these metrics.
- **Cohort = the month a seller joined** (`sellers.created_at`), not the month of
  their first order. Sellers who join and never sell count in the cohort size
  and drag down retention, which is the point: activation is part of retention.
- **Buyer region** is derived from buyer country by the `buyer_region` macro:
  US and CA are North America; DE, FR, NL, ES and IT are EU; GB is UK; BR is
  LATAM; JP is APAC; anything else is Other.

## Known limitations

- **Month 0 is a partial month.** A seller who joins on the 28th has three days
  of month-0 revenue. Net revenue retention against month 0 therefore runs
  high for months 1+. A production version would anchor on the first full
  month, or on trailing-90-day revenue.
- **Cohorts older than the data window have no baseline.** Their
  `month_zero_revenue_usd_minor` is null, so their revenue retention is null
  rather than a misleading number.
- **A month with no cohort activity has no row.** Retention charts should treat a
  missing month as zero, not interpolate it.
- **FX booking rate.** GMV uses the reference rate on the order date. The
  difference from the processor's settlement rate is realized FX (account 5200)
  and lands in contribution, not in GMV.
