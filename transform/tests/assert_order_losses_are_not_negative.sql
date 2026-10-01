-- Refunds and dispute losses are charges to the seller: positive USD cents.
-- A negative value means a sign error between the ledger and the metrics.
select
    order_id,
    refunded_usd_minor,
    dispute_loss_to_seller_usd_minor
from {{ ref('fct_orders') }}
where refunded_usd_minor < 0 or dispute_loss_to_seller_usd_minor < 0
