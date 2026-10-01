-- Kiln's orders matched to processor charges, one row per order or unmatched charge.
--   matched                    order and charge agree on currency and amount
--   amount_mismatch            both exist but disagree
--   order_without_settlement   Kiln recorded a paid order the processor never settled
--   settlement_without_order   the processor settled a charge Kiln has no order for
with orders as (
    select * from {{ ref('stg_app_db__orders') }}
),

charges as (
    select * from {{ ref('int_settlement_events') }}
    where event_type = 'charge'
)

select
    charges.settlement_event_id,
    orders.currency as order_currency,
    orders.amount_minor as order_amount_minor,
    charges.presentment_currency as settled_currency,
    charges.presentment_amount_minor as settled_amount_minor,
    coalesce(orders.order_id, charges.kiln_order_id) as order_id,
    coalesce(charges.processor, orders.processor) as processor,
    coalesce(charges.processor_reference, orders.processor_charge_ref) as charge_reference,
    coalesce(date(orders.created_at), charges.event_date) as match_date,
    case
        when charges.settlement_event_id is null then 'order_without_settlement'
        when orders.order_id is null then 'settlement_without_order'
        when
            orders.currency != charges.presentment_currency
            or orders.amount_minor != charges.presentment_amount_minor then 'amount_mismatch'
        else 'matched'
    end as match_status
from orders
full outer join charges
    on orders.order_id = charges.kiln_order_id
