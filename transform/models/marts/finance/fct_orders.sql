{#
  One row per Kiln order with its economics in USD.

  Kiln is an agent, not the principal: it arranges the sale between seller and
  buyer and never controls the product. So Kiln's revenue is its platform fee,
  not the order value. GMV is the order value at the booking rate, and the
  seller's share passes through Kiln's books as a liability (seller payable).

  contribution_usd_minor = platform fee revenue - processing fees - realized FX
  loss (+ gain). Refunds and disputes hit the seller's balance, not Kiln's P&L,
  unless they turn into bad debt.
#}
with orders as (
    select * from {{ ref('stg_app_db__orders') }}
),

rates as (
    select * from {{ ref('int_fx_rates_daily') }}
),

ledger as (
    select
        kiln_order_id,
        sum(if(account_code = '4000', -amount_usd_minor, 0)) as platform_fee_revenue_usd_minor,
        sum(if(account_code = '5000', amount_usd_minor, 0)) as processing_fee_usd_minor,
        sum(if(account_code = '5200', amount_usd_minor, 0)) as realized_fx_loss_usd_minor,
        sum(if(account_code = '2000' and source_event_type = 'refund', amount_usd_minor, 0))
            as refunded_usd_minor,
        sum(if(
            account_code = '2000' and source_event_type in ('dispute', 'dispute_fee'),
            amount_usd_minor, 0
        ))
        - sum(if(
            account_code = '2000' and source_event_type = 'dispute_reversal',
            -amount_usd_minor, 0
        )) as dispute_loss_to_seller_usd_minor,
        countif(source_event_type = 'charge') > 0 as is_settled
    from {{ ref('fct_ledger_entries') }}
    where kiln_order_id is not null
    group by kiln_order_id
)

select
    orders.order_id,
    orders.seller_id,
    orders.product_id,
    orders.buyer_country,
    orders.processor,
    orders.currency,
    orders.order_type,
    orders.plan_id,
    orders.created_at,
    orders.amount_minor as order_amount_minor,
    date(orders.created_at) as order_date,
    {{ to_usd_minor('orders.amount_minor', 'orders.currency', 'rates.units_per_usd') }}
        as gmv_usd_minor,
    coalesce(ledger.platform_fee_revenue_usd_minor, 0) as platform_fee_revenue_usd_minor,
    coalesce(ledger.processing_fee_usd_minor, 0) as processing_fee_usd_minor,
    coalesce(ledger.realized_fx_loss_usd_minor, 0) as realized_fx_loss_usd_minor,
    coalesce(ledger.refunded_usd_minor, 0) as refunded_usd_minor,
    coalesce(ledger.dispute_loss_to_seller_usd_minor, 0) as dispute_loss_to_seller_usd_minor,
    coalesce(ledger.platform_fee_revenue_usd_minor, 0)
    - coalesce(ledger.processing_fee_usd_minor, 0)
    - coalesce(ledger.realized_fx_loss_usd_minor, 0) as contribution_usd_minor,
    coalesce(ledger.is_settled, false) as is_settled
from orders
left join rates
    on
        orders.currency = rates.currency
        and date(orders.created_at) = rates.rate_date
left join ledger
    on orders.order_id = ledger.kiln_order_id
