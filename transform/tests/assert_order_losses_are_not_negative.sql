-- Refunds and dispute losses are charges to the seller: positive USD cents.
-- The one legitimate exception is a won chargeback whose original chargeback
-- never reached us (a missing settlement file): that order is listed in
-- fct_orphan_dispute_reversals, which warns. Any other negative value is a
-- sign or mapping error between the ledger and the metrics.
select
    orders.order_id,
    orders.refunded_usd_minor,
    orders.dispute_loss_to_seller_usd_minor
from {{ ref('fct_orders') }} as orders
left join {{ ref('fct_orphan_dispute_reversals') }} as orphans
    on orders.order_id = orphans.kiln_order_id
where
    orders.refunded_usd_minor < 0
    or (orders.dispute_loss_to_seller_usd_minor < 0 and orphans.kiln_order_id is null)
