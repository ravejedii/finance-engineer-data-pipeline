# Data issues: the answer key

The generator plants these problems on purpose and records every affected
row in `data/truth/expected_issues.json` (IDs, not just counts). Each one
must be caught by a test, or show up in an exceptions or break table, by
the phase listed. Nothing disappears silently.

Counts are for seed 42. `tests/test_generator.py` fails if the **Small**
column stops matching the generator. In CI, `scripts/check_issue_coverage.py`
reads the answer key and checks the warehouse issue by issue, by row ID where
the issue has IDs.

## Injected problems and system mismatches

| Issue | Small | Full | What it is | Caught by |
|---|---|---|---|---|
| `processor_a_duplicate_rows` | 3 | 794 | Exact copy of a balance transaction, re-sent in the next day's file. | `stg_processor_a__balance_transactions` dedupes; `unique` test + coverage check |
| `processor_a_late_rows` | 5 | 1,324 | Row first appears 1–3 files after its `created_utc` date. | Loader lands them; staging keys on id, not file; coverage check |
| `processor_a_restated_rows` | 3 | 530 | Same `balance_transaction_id` re-sent later with a corrected `fee` and `net`. The later version is correct. | Staging keeps the latest file's version; coverage check compares fees |
| `processor_a_malformed_rows` | 2 | 5 | `gross` = `N/A`, or empty `balance_transaction_id`. | Landed verbatim; `stg_exceptions` with reason; coverage check |
| `processor_a_truncated_rows` | 1 | 3 | Line cut off after 9 of 14 fields (a partial write). | Loader `load_exceptions` with line number and raw text; coverage check |
| `processor_b_duplicate_rows` | 2 | 102 | Exact copy of a row, re-sent in the next batch file. | `stg_processor_b__settlement_details` keeps the original batch's copy; coverage check |
| `processor_b_late_rows` | 2 | 203 | Row booked on one CET day that arrives in the next day's batch. | Settles in the later batch; batch payouts still net (generator test); coverage check |
| `processor_b_malformed_rows` | 1 | 5 | `Net Credit (NC)` written with a decimal comma (`12,34`). | Landed verbatim; `stg_exceptions` with reason; coverage check |
| `processor_b_missing_batch_file` | 1 | 1 | One batch file never arrives (small: batch 65, 2025-08-04; full: batch 275, 2024-12-31). Its payout still reaches the EUR bank account. | `fct_processor_b_batch_gaps` (warn test) + `fct_payout_reconciliation` status `bank_receipt_without_report`; coverage check |
| `orders_without_settlement` | 4 | 169 | Kiln order marked `paid` that no processor settled. The seller was still credited. | `fct_order_settlement_matches` status `order_without_settlement`; coverage check |
| `settlements_without_order` | 4 | 101 | Processor charge whose Kiln order reference doesn't exist in the app DB. | `fct_order_settlement_matches` status `settlement_without_order`; ledger suspense account 2100 |
| `timezone_boundary_planted` | 2 | 2 | Two orders at 23:30 UTC on a month-end, one per processor. Processor A reports the UTC date; processor B reports the next local date and month. | Staging converts CET/CEST to UTC; coverage check confirms both land on the UTC date |
| `fx_reference_weekend_gaps` | 175 | 780 | Reference rates publish on business days only (weekend days × 5 currencies). | `int_fx_rates_daily` carries the last published rate forward; coverage check counts filled days |

## Business facts the pipeline must handle

These come from how the business works; none of them is injected damage.

| Issue | Small | Full | What it is | Caught by |
|---|---|---|---|---|
| `refunds_after_seller_payout` | 13 | 5,245 | Refund created after the seller was already paid. | Seller payable goes down after payout (`fct_ledger_entries`) |
| `disputes_after_seller_payout` | 11 | 1,767 | Dispute opened after the seller was already paid. | Seller payable and `fct_seller_balance_aging` |
| `negative_seller_balances_at_end` | 4 | 181 | Sellers who owe Kiln at period end (full: 129 negative for 90+ days, total −$6,551.09). | `fct_seller_balance_aging` + bad-debt close entry |
| `seller_payouts_failed` | 5 | 679 | Seller payout rejected by the receiving bank; funds return 3 days later. | Ledger `seller_payout_return` entries; coverage check counts them |
| `seller_payouts_reversed` | 2 | 240 | Seller payout that settled and was later recalled 10 days after. | Ledger `seller_payout_return` entries; coverage check counts them |
| `processor_a_payouts_failed` | 3 | 3 | Processor A payout to Kiln failed; funds returned to the processor balance and never reached the bank. | `fct_payout_reconciliation` status `failed_and_returned`; coverage check |
| `timezone_boundary_natural` | 31 | 7,180 | Every processor B row whose local date differs from its UTC date. | Staging UTC conversion |

## Consequences to expect downstream

- **Two kinds of bad rows, two places they're caught.** A row that can't
  be split into the contracted columns (truncated) never reaches raw: the
  loader sends it to `load_exceptions`. A row that splits fine but has a bad
  value (`N/A`, `12,34`) lands in raw as text, and staging routes it to
  exceptions. Raw stays a faithful copy of what arrived.
- **Malformed rows are money that goes missing from the source.** A
  malformed row is the only copy of that transaction, so after it lands in
  exceptions, reconciliation should show a break equal to its amount. That
  break is correct. A reconciliation that shows zero breaks would be hiding
  the problem.
- **The missing processor B file** shows up three times: as a gap in batch
  numbers; as a EUR bank receipt (`PAYOUT-0065` small / `PAYOUT-0275`
  full) with no settlement detail behind it; and, at full scale, as two won
  chargebacks (orders 80985 and 87735) whose chargeback sat in the missing
  file but whose reversal arrived later. The ledger sees only the reversal,
  so those orders show a negative dispute loss. `fct_orphan_dispute_reversals`
  lists them (warn), and the coverage check asserts exactly these orders.
  This was found by the full-scale build, not designed in up front.
- **Issues can overlap.** The generator picks each issue independently, so at
  full scale one processor B line is both late and malformed. Malformed wins:
  it goes to exceptions, and the coverage check expects it there, not in
  staging.
- **Small scale runs short** (four months), so no seller in it is negative
  for 90+ days (2 sellers are negative for 30+). CI exercises bad-debt
  recognition by setting the aging-threshold var to 30. Phase 4 unit tests
  cover the 90-day default with hand-built cases.
- **Interpretation to confirm:** "the same transaction falls on different
  dates in A and B" is modeled as the same UTC instant reported on different
  dates, because processor A reports in UTC and processor B in local
  CET/CEST time. No single charge appears in both processors.
