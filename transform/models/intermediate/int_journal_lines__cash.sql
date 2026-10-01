-- Double-entry lines for cash movements outside the processors, in USD minor units.
--
-- Processor payout lands in the bank (from the bank statement):
--   Dr cash in bank           Cr cash in transit
--   EUR receipts clear transit at the USD amount the payout was booked at, so a
--   matched payout nets transit to exactly zero. A receipt with no payout behind
--   it (processor B's missing batch file) is converted at the booking rate and
--   leaves transit negative: that is the reconciliation break, visible on purpose.
--
-- Seller payout (from Kiln's payout records):
--   initiated:  Dr seller payable   Cr cash in bank (USD)
--   returned:   Dr cash in bank     Cr seller payable   (failed or recalled payouts)
with bank as (
    select * from {{ ref('stg_bank__statement_lines') }}
),

payouts_booked as (
    select
        processor_reference,
        amount_usd_minor as transit_usd_minor
    from {{ ref('int_journal_lines__settlements') }}
    where source_event_type = 'payout' and account_code in ('1050', '1060')
),

rates as (
    select * from {{ ref('int_fx_rates_daily') }}
),

processor_receipts as (
    select
        bank.bank_transaction_id,
        bank.booking_date,
        bank.end_to_end_reference,
        bank.currency,
        bank.amount_minor,
        if(bank.currency = 'USD', '1100', '1110') as bank_account,
        if(bank.currency = 'USD', '1050', '1060') as transit_account,
        coalesce(
            payouts_booked.transit_usd_minor,
            {{ to_usd_minor('bank.amount_minor', 'bank.currency', 'rates.units_per_usd') }}
        ) as amount_usd_minor
    from bank
    left join payouts_booked
        on bank.end_to_end_reference = payouts_booked.processor_reference
    left join rates
        on
            bank.currency = rates.currency
            and bank.booking_date = rates.rate_date
    where bank.counterparty_name in ('PROCESSOR A PAYOUTS', 'PROCESSOR B SETTLEMENT')
),

seller_payout_events as (
    select
        seller_payout_id,
        seller_id,
        'seller_payout' as event_type,
        date(initiated_at) as posting_date,
        initiated_at as occurred_at,
        amount_minor
    from {{ ref('stg_app_db__seller_payouts') }}

    union all

    select
        seller_payout_id,
        seller_id,
        'seller_payout_return' as event_type,
        date(returned_at) as posting_date,
        returned_at as occurred_at,
        -amount_minor as amount_minor
    from {{ ref('stg_app_db__seller_payouts') }}
    where returned_at is not null
),

receipt_lines as (
    select
        to_hex(md5(concat('bank|', processor_receipts.bank_transaction_id))) as journal_entry_id,
        processor_receipts.booking_date as posting_date,
        timestamp(processor_receipts.booking_date) as occurred_at,
        line.account_code,
        line.amount_usd_minor,
        'bank' as source_system,
        'processor_payout_receipt' as source_event_type,
        processor_receipts.bank_transaction_id as source_reference,
        processor_receipts.end_to_end_reference as processor_reference,
        cast(null as int64) as kiln_order_id,
        cast(null as int64) as seller_id,
        processor_receipts.currency as original_currency,
        processor_receipts.amount_minor as original_amount_minor
    from processor_receipts
    cross join unnest([
        struct(
            processor_receipts.bank_account as account_code,
            processor_receipts.amount_usd_minor as amount_usd_minor
        ),
        struct(processor_receipts.transit_account, -processor_receipts.amount_usd_minor)
    ]) as line
),

seller_payout_lines as (
    select
        to_hex(md5(concat(
            'app_db|', seller_payout_events.event_type, '|',
            cast(seller_payout_events.seller_payout_id as string)
        )))
            as journal_entry_id,
        seller_payout_events.posting_date,
        seller_payout_events.occurred_at,
        line.account_code,
        line.amount_usd_minor,
        'app_db' as source_system,
        seller_payout_events.event_type as source_event_type,
        cast(seller_payout_events.seller_payout_id as string) as source_reference,
        concat('SP-', cast(seller_payout_events.seller_payout_id as string))
            as processor_reference,
        cast(null as int64) as kiln_order_id,
        seller_payout_events.seller_id,
        'USD' as original_currency,
        seller_payout_events.amount_minor as original_amount_minor
    from seller_payout_events
    cross join unnest([
        struct('2000' as account_code, seller_payout_events.amount_minor as amount_usd_minor),
        struct('1100', -seller_payout_events.amount_minor)
    ]) as line
)

select
    *,
    to_hex(md5(concat(journal_entry_id, '|', account_code))) as ledger_line_id
from receipt_lines

union all

select
    *,
    to_hex(md5(concat(journal_entry_id, '|', account_code))) as ledger_line_id
from seller_payout_lines
