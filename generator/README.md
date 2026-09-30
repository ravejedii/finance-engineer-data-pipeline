# Synthetic data generator

```bash
uv run python -m generator --scale small          # ~4 months, runs in well under a second
uv run python -m generator --scale full           # 18 months, ~350k orders, ~25 s
uv run python -m generator --scale full --seed 7  # a different, equally valid world
```

Output goes to `data/` (gitignored). Every run deletes and rewrites
`data/raw/` and `data/truth/`. Same seed, same bytes.

## How it works

1. **`simulate.py`: the truth.** Walks one UTC day at a time: sellers join
   and churn, buyers order, refunds and disputes happen later, sellers get
   paid weekly. This is what actually happened, before any report exists.
2. **`mess.py` (system mismatches):** marks some orders as never settled and
   adds processor charges Kiln has no order for.
3. **`render.py`:** writes each system's view of the truth in its own
   format: processor A, processor B, the bank, the FX feed, the app DB.
4. **`mess.py` (file mess):** duplicates, late rows, restated rows,
   malformed rows, and one missing file. Each is logged with row IDs in
   `data/truth/expected_issues.json`. See [DATA_ISSUES.md](../DATA_ISSUES.md).

Money is integer minor units or `Decimal` throughout, rounded half-even.
No floats touch money.

## Raw files

| Path | Grain | Notes |
|---|---|---|
| `processor_a/balance_transactions_<date>.csv` | one balance movement | UTC. Amounts are **decimal strings in USD** (`12.34`), not minor units. `reporting_category`: charge, refund, dispute, dispute_reversal, payout, payout_reversal. Order link is in the `payment_metadata[kiln_order_id]` column (brackets in the name). A file arrives every day, header-only if empty. |
| `processor_a/payouts_<date>.csv` | one payout **status change** | A payout appears as `in_transit`, then again as `paid` or `failed`. The latest status wins. |
| `processor_b/settlement_detail_report_batch_<nnnn>.csv` | one settlement line | One batch per **CET/CEST** day. `Creation Date` is local time and `TimeZone` says which. Gross in the buyer's currency, net and fees in EUR, decimal strings. Debit and credit are separate columns. Order link: `Merchant Reference` = `KILN-<order_id>`. The batch closes with a `MerchantPayout` row. |
| `bank/bank_statement_<date>.csv` | one bank movement | Business days only. Two accounts: `KILN-USD-0001` (processor A payouts in, seller payouts out) and `KILN-EUR-0001` (processor B settlements in). Signed decimal amounts. |
| `fx/reference_rates_<date>.csv` | currency × business day | Quoted as **units of currency per 1 USD** (e.g. JPY 150.25). To get USD, divide. |
| `app_db/*.csv` | one row per entity | Full nightly extract of Kiln's own database. Amounts are **integer minor units**; timestamps are ISO-8601 UTC with `Z`. Tables: plans, plan_fixed_fees, sellers, seller_plan_changes (effective-dated history), products, orders, refunds, seller_payouts. |

## Business rules baked into the truth

These are policies of the fictional business. The ledger has to encode the
same rules:

- **Seller balances are in USD.** Kiln converts each order at the reference
  rate published on or before the order date, so Kiln (not the seller)
  carries the gap between that rate and the processor's rate.
- **Platform fee** = plan percentage + fixed fee per currency, taken at
  order time and not refunded. A refund reduces the seller's balance by the
  full refunded amount.
- **Dispute fees are passed to the seller.** A lost dispute costs the seller
  the disputed amount plus the fee. A won dispute returns the amount but not
  the fee.
- **Seller payouts** run weekly on Mondays for balances of $10 or more.
  Each order's proceeds are held for 7 days first. Balances can go negative
  when refunds or disputes arrive after a payout.
- **Processor A** settles in USD, available T+2, and pays Kiln daily.
  **Processor B** settles in EUR and pays per daily batch. A negative batch
  carries forward to the next one.
