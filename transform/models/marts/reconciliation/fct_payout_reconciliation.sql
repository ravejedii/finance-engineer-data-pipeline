{#
  Every processor payout to Kiln matched to Kiln's bank statement by reference,
  one row per payout reference.
    matched                       bank received exactly what the processor paid out
    amount_break                  both sides exist, amounts differ beyond tolerance
    failed_and_returned           processor reports the payout failed; no bank receipt expected
    in_transit                    paid out, not yet on a bank statement (expected near period end)
    bank_receipt_without_report   money arrived with no processor report behind it,
                                  e.g. a settlement file that never arrived
#}
with payouts as (
    select
        processor,
        processor_reference as payout_reference,
        settlement_currency as currency,
        event_date as payout_date,
        -net_settlement_minor as payout_amount_minor
    from {{ ref('int_settlement_events') }}
    where event_type = 'payout'
),

failures as (
    select distinct processor_reference as payout_reference
    from {{ ref('int_settlement_events') }}
    where event_type = 'payout_reversal'
),

receipts as (
    select
        end_to_end_reference as payout_reference,
        currency,
        if(counterparty_name = 'PROCESSOR A PAYOUTS', 'processor_a', 'processor_b') as processor,
        min(booking_date) as booking_date,
        sum(amount_minor) as bank_amount_minor
    from {{ ref('stg_bank__statement_lines') }}
    where counterparty_name in ('PROCESSOR A PAYOUTS', 'PROCESSOR B SETTLEMENT')
    group by payout_reference, processor, currency
)

select
    payouts.payout_date,
    receipts.booking_date as bank_booking_date,
    payouts.payout_amount_minor,
    receipts.bank_amount_minor,
    coalesce(payouts.payout_reference, receipts.payout_reference) as payout_reference,
    coalesce(payouts.processor, receipts.processor) as processor,
    coalesce(payouts.currency, receipts.currency) as currency,
    coalesce(receipts.bank_amount_minor, 0) - coalesce(payouts.payout_amount_minor, 0)
        as difference_minor,
    case
        when payouts.payout_reference is null then 'bank_receipt_without_report'
        when failures.payout_reference is not null and receipts.payout_reference is null
            then 'failed_and_returned'
        when receipts.payout_reference is null then 'in_transit'
        when
            abs(receipts.bank_amount_minor - payouts.payout_amount_minor)
            > {{ var('recon_tolerance_minor') }} then 'amount_break'
        else 'matched'
    end as recon_status
from payouts
full outer join receipts
    on payouts.payout_reference = receipts.payout_reference
left join failures
    on payouts.payout_reference = failures.payout_reference
