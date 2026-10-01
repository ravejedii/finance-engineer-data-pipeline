{#
  Daily three-way reconciliation by processor and settlement currency.

  1. Processor vs ledger: the net the processor reported for the day must equal
     what the ledger booked to cash-at-processor, in the original currency.
     Any difference means a settlement event was dropped or double-posted.
  2. Processor vs bank: payouts dated that day, and how many of them have a
     reconciliation status that needs attention.
  break_status is 'break' if (1) is outside var('recon_tolerance_minor') or (2)
  has any amount break or unexplained bank receipt.
#}
with processor_daily as (
    select
        event_date as recon_date,
        processor,
        settlement_currency as currency,
        sum(net_settlement_minor) as processor_net_minor
    from {{ ref('int_settlement_events') }}
    group by recon_date, processor, currency
),

ledger_daily as (
    select
        posting_date as recon_date,
        source_system as processor,
        original_currency as currency,
        sum(original_amount_minor) as ledger_cash_minor
    from {{ ref('fct_ledger_entries') }}
    where account_code in ('1000', '1010')
    group by recon_date, processor, currency
),

payout_daily as (
    select
        processor,
        currency,
        coalesce(payout_date, bank_booking_date) as recon_date,
        sum(coalesce(payout_amount_minor, 0)) as payouts_reported_minor,
        sum(coalesce(bank_amount_minor, 0)) as bank_received_minor,
        countif(recon_status = 'matched') as payouts_matched,
        countif(recon_status = 'in_transit') as payouts_in_transit,
        countif(recon_status = 'failed_and_returned') as payouts_failed,
        countif(recon_status in ('amount_break', 'bank_receipt_without_report'))
            as payout_breaks
    from {{ ref('fct_payout_reconciliation') }}
    group by recon_date, processor, currency
),

keys as (
    select
        recon_date,
        processor,
        currency
    from processor_daily
    union distinct
    select
        recon_date,
        processor,
        currency
    from ledger_daily
    union distinct
    select
        recon_date,
        processor,
        currency
    from payout_daily
)

select
    keys.recon_date,
    keys.processor,
    keys.currency,
    coalesce(processor_daily.processor_net_minor, 0) as processor_net_minor,
    coalesce(ledger_daily.ledger_cash_minor, 0) as ledger_cash_minor,
    coalesce(ledger_daily.ledger_cash_minor, 0) - coalesce(processor_daily.processor_net_minor, 0)
        as ledger_vs_processor_minor,
    coalesce(payout_daily.payouts_reported_minor, 0) as payouts_reported_minor,
    coalesce(payout_daily.bank_received_minor, 0) as bank_received_minor,
    coalesce(payout_daily.payouts_matched, 0) as payouts_matched,
    coalesce(payout_daily.payouts_in_transit, 0) as payouts_in_transit,
    coalesce(payout_daily.payouts_failed, 0) as payouts_failed,
    coalesce(payout_daily.payout_breaks, 0) as payout_breaks,
    if(
        abs(
            coalesce(ledger_daily.ledger_cash_minor, 0)
            - coalesce(processor_daily.processor_net_minor, 0)
        )
        > {{ var('recon_tolerance_minor') }}
        or coalesce(payout_daily.payout_breaks, 0) > 0,
        'break',
        'ok'
    ) as break_status
from keys
left join processor_daily
    on
        keys.recon_date = processor_daily.recon_date
        and keys.processor = processor_daily.processor
        and keys.currency = processor_daily.currency
left join ledger_daily
    on
        keys.recon_date = ledger_daily.recon_date
        and keys.processor = ledger_daily.processor
        and keys.currency = ledger_daily.currency
left join payout_daily
    on
        keys.recon_date = payout_daily.recon_date
        and keys.processor = payout_daily.processor
        and keys.currency = payout_daily.currency
