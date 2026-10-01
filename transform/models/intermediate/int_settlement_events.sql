-- One row per money movement at a processor, both processors in one shape.
-- Grain: a single change to Kiln's balance at one processor (a charge, a refund,
-- a dispute, a dispute fee, a dispute reversal, a payout, a failed payout).
-- Signs are from Kiln's point of view: money into the processor balance is +.
with processor_a as (
    select
        balance_transaction_id as settlement_event_id,
        'processor_a' as processor,
        case reporting_category
            when 'charge' then 'charge'
            when 'refund' then 'refund'
            when 'dispute' then 'dispute'
            when 'dispute_reversal' then 'dispute_reversal'
            when 'payout' then 'payout'
            when 'payout_reversal' then 'payout_reversal'
        end as event_type,
        created_at as occurred_at,
        source_id as processor_reference,
        automatic_payout_id as payout_reference,
        cast(null as int64) as batch_number,
        kiln_order_id,
        presentment_currency,
        presentment_amount_minor,
        settlement_currency,
        gross_minor as gross_settlement_minor,
        fee_minor,
        net_minor as net_settlement_minor,
        _source_file
    from {{ ref('stg_processor_a__balance_transactions') }}
),

processor_b as (
    select
        settlement_line_id as settlement_event_id,
        'processor_b' as processor,
        case record_type
            when 'Settled' then 'charge'
            when 'Refunded' then 'refund'
            when 'Chargeback' then 'dispute'
            when 'Fee' then 'dispute_fee'
            when 'ChargebackReversed' then 'dispute_reversal'
            when 'MerchantPayout' then 'payout'
        end as event_type,
        created_at as occurred_at,
        coalesce(psp_reference, modification_reference) as processor_reference,
        if(record_type = 'MerchantPayout', modification_reference, null) as payout_reference,
        batch_number,
        kiln_order_id,
        if(record_type = 'Fee', null, gross_currency) as presentment_currency,
        if(record_type = 'Fee', null, gross_minor) as presentment_amount_minor,
        net_currency as settlement_currency,
        -- Processor B reports net plus separate fee columns, so gross = net + fees.
        -- A chargeback "Fee" row is all fee: gross 0, fee 15.00, net -15.00.
        net_minor + fees_minor as gross_settlement_minor,
        fees_minor as fee_minor,
        net_minor as net_settlement_minor,
        _source_file
    from (
        select
            *,
            if(
                record_type = 'Fee',
                -net_minor,
                commission_minor + markup_minor + scheme_fees_minor + interchange_minor
            ) as fees_minor
        from {{ ref('stg_processor_b__settlement_details') }}
    )
)

select
    *,
    date(occurred_at) as event_date
from processor_a

union all

select
    *,
    date(occurred_at) as event_date
from processor_b
