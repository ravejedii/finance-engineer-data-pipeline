-- Double-entry lines for every processor settlement event, in USD minor units.
-- Signed amount: positive = debit, negative = credit. Each event's lines sum to 0.
--
-- Charge (the core pattern):
--   Dr cash at processor      net settled, converted at the booking rate
--   Dr processing fees        processor fee
--   Cr seller payable         order amount - platform fee, at the booking rate
--   Cr platform fee revenue   platform fee, at the booking rate
--   Dr/Cr FX gain/loss        whatever is left: the gap between Kiln's booking
--                             rate and the rate the processor actually used
-- Refunds, disputes and reversals move seller payable by the presentment
-- amount (dispute fees are passed to the seller). Charges with no Kiln order
-- go to a suspense account instead of seller payable and revenue.
with events as (
    select * from {{ ref('int_settlement_events') }}
),

orders as (
    select * from {{ ref('stg_app_db__orders') }}
),

rates as (
    select * from {{ ref('int_fx_rates_daily') }}
),

converted as (
    select
        events.*,
        orders.seller_id,
        orders.order_id is not null as is_matched,
        {{ to_usd_minor('events.net_settlement_minor', 'events.settlement_currency',
                        'settle_rate.units_per_usd') }} as net_usd,
        {{ to_usd_minor('events.fee_minor', 'events.settlement_currency',
                        'settle_rate.units_per_usd') }} as fee_usd,
        {{ to_usd_minor('events.presentment_amount_minor', 'events.presentment_currency',
                        'present_rate.units_per_usd') }} as booked_usd,
        {{ to_usd_minor('orders.platform_fee_minor', 'orders.currency',
                        'present_rate.units_per_usd') }} as platform_fee_usd
    from events
    left join orders
        on events.kiln_order_id = orders.order_id
    left join rates as settle_rate
        on
            events.settlement_currency = settle_rate.currency
            and events.event_date = settle_rate.rate_date
    left join rates as present_rate
        on
            events.presentment_currency = present_rate.currency
            and events.event_date = present_rate.rate_date
),

lines as (
    select
        converted.*,
        line.account_code,
        line.amount_usd_minor
    from converted
    cross join unnest(
        case
            when event_type in ('payout', 'payout_reversal')
                then [
                    struct(
                        if(processor = 'processor_a', '1000', '1010') as account_code,
                        net_usd as amount_usd_minor
                    ),
                    struct(if(processor = 'processor_a', '1050', '1060'), -net_usd)
                ]
            when not is_matched
                then [
                    struct(
                        if(processor = 'processor_a', '1000', '1010') as account_code,
                        net_usd as amount_usd_minor
                    ),
                    struct(if(event_type = 'charge', '5000', '5100'), fee_usd),
                    struct('2100', -(net_usd + fee_usd))
                ]
            when event_type = 'charge'
                then [
                    struct(
                        if(processor = 'processor_a', '1000', '1010') as account_code,
                        net_usd as amount_usd_minor
                    ),
                    struct('5000', fee_usd),
                    struct('2000', -(booked_usd - platform_fee_usd)),
                    struct('4000', -platform_fee_usd),
                    struct('5200', -(net_usd + fee_usd - booked_usd))
                ]
            when event_type = 'dispute_fee'
                then [
                    struct(
                        if(processor = 'processor_a', '1000', '1010') as account_code,
                        net_usd as amount_usd_minor
                    ),
                    struct('2000', fee_usd)
                ]
            else [  -- refund, dispute, dispute_reversal: seller bears amount and any fee
                struct(
                    if(processor = 'processor_a', '1000', '1010') as account_code,
                    net_usd as amount_usd_minor
                ),
                struct('2000', -(booked_usd - fee_usd)),
                struct('5200', -(net_usd - booked_usd + fee_usd))
            ]
        end
    ) as line
)

select
    event_date as posting_date,
    occurred_at,
    account_code,
    amount_usd_minor,
    processor as source_system,
    event_type as source_event_type,
    settlement_event_id as source_reference,
    processor_reference,
    kiln_order_id,
    seller_id,
    settlement_currency as original_currency,
    net_settlement_minor as original_amount_minor,
    to_hex(md5(concat(processor, '|', settlement_event_id))) as journal_entry_id,
    to_hex(md5(concat(processor, '|', settlement_event_id, '|', account_code)))
        as ledger_line_id
from lines
-- Zero lines are noise. A null amount (a missing FX rate) is kept so tests catch it.
where amount_usd_minor != 0 or amount_usd_minor is null
