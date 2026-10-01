# Data issues: the answer key

The generator plants these problems on purpose and records every affected
row in `data/truth/expected_issues.json` (IDs, not just counts). Each one
must be caught by a test, or show up in an exceptions or break table, by
the phase listed. Nothing disappears silently.

Counts are for seed 42. `tests/test_generator.py` fails if the **Small**
column stops matching the generator.

## Injected problems and system mismatches

| Issue | Small | Full | What it is | Caught in |
|---|---|---|---|---|
| `processor_a_duplicate_rows` | 3 | 794 | Exact copy of a balance transaction, re-sent in the next day's file. | Phase 3 staging dedupe; test that each `balance_transaction_id` is unique |
| `processor_a_late_rows` | 5 | 1,324 | Row first appears 1–3 files after its `created_utc` date. | Phase 2 lands it; Phase 3 incremental lookback picks it up |
| `processor_a_restated_rows` | 3 | 530 | Same `balance_transaction_id` re-sent later with a corrected `fee` and `net`. The later version is correct. | Phase 3 staging keeps the latest version by file date |
| `processor_a_malformed_rows` | 2 | 5 | `gross` = `N/A`, or empty `balance_transaction_id`. | Phase 2 lands them verbatim; Phase 3 staging routes them to exceptions |
| `processor_a_truncated_rows` | 1 | 3 | Line cut off after 9 of 14 fields (a partial write). | Phase 2 loader exceptions table |
| `processor_b_duplicate_rows` | 2 | 102 | Exact copy of a row, re-sent in the next batch file. | Phase 3 staging dedupe on (Psp Reference, Type, Modification Reference) |
| `processor_b_late_rows` | 2 | 203 | Row booked on one CET day that arrives in the next day's batch. | Phase 3; settles in the later batch, so the ledger follows the batch |
| `processor_b_malformed_rows` | 1 | 5 | `Net Credit (NC)` written with a decimal comma (`12,34`). | Phase 2 lands them verbatim; Phase 3 staging routes them to exceptions |
| `processor_b_missing_batch_file` | 1 | 1 | One batch file never arrives (small: batch 65, 2025-08-04; full: batch 275, 2024-12-31). Its payout still reaches the EUR bank account. | Phase 5: batch-number gap test, plus a bank-vs-settlement recon break |
| `orders_without_settlement` | 4 | 169 | Kiln order marked `paid` that no processor settled. The seller was still credited. | Phase 5 order-to-settlement matching |
| `settlements_without_order` | 4 | 101 | Processor charge whose Kiln order reference doesn't exist in the app DB. | Phase 5 order-to-settlement matching |
| `timezone_boundary_planted` | 2 | 2 | Two orders at 23:30 UTC on a month-end, one per processor. Processor A reports the UTC date; processor B reports the next local date and month. | Phase 3 staging converts processor B to UTC; test that both land in the same month |
| `fx_reference_weekend_gaps` | 175 | 780 | Reference rates publish on business days only (weekend days × 5 currencies). | Phase 4: FX booking uses the latest published rate on or before the date |

## Business facts the pipeline must handle

These come from how the business works; none of them is injected damage.

| Issue | Small | Full | What it is | Caught in |
|---|---|---|---|---|
| `refunds_after_seller_payout` | 13 | 5,245 | Refund created after the seller was already paid. | Phase 4 seller balances go down after payout |
| `disputes_after_seller_payout` | 11 | 1,767 | Dispute opened after the seller was already paid. | Phase 4 seller balances and bad debt |
| `negative_seller_balances_at_end` | 4 | 181 | Sellers who owe Kiln at period end (full: 129 negative for 90+ days, total −$6,551.09). | Phase 4 bad-debt aging |
| `seller_payouts_failed` | 5 | 679 | Seller payout rejected by the receiving bank; funds return 3 days later. | Phase 3 ledger reverses it; Phase 5 bank recon |
| `seller_payouts_reversed` | 2 | 240 | Seller payout that settled and was later recalled 10 days after. | Phase 3 ledger; Phase 5 bank recon |
| `processor_a_payouts_failed` | 3 | 3 | Processor A payout to Kiln failed; funds returned to the processor balance and never reached the bank. | Phase 5 processor-vs-bank recon |
| `timezone_boundary_natural` | 31 | 7,180 | Every processor B row whose local date differs from its UTC date. | Phase 3 UTC conversion |

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
- **The missing processor B file** shows up twice: as a gap in batch
  numbers, and as a EUR bank receipt (`PAYOUT-0065` small / `PAYOUT-0275`
  full) with no settlement detail behind it.
- **Small scale runs short** (four months), so no seller in it is negative
  for 90+ days (2 sellers are negative for 30+). CI exercises bad-debt
  recognition by setting the aging-threshold var to 30. Phase 4 unit tests
  cover the 90-day default with hand-built cases.
- **Interpretation to confirm:** "the same transaction falls on different
  dates in A and B" is modeled as the same UTC instant reported on different
  dates, because processor A reports in UTC and processor B in local
  CET/CEST time. No single charge appears in both processors.
