-- Company-level metrics by month: one row per month, every metric in
-- METRICS.md with its exact definition. The dashboards' headline numbers.
with totals as (
    select
        order_month,
        sum(orders) as orders,
        sum(refunded_orders) as refunded_orders,
        sum(disputes) as disputes,
        sum(gmv_usd_minor) as gmv_usd_minor,
        sum(net_revenue_usd_minor) as net_revenue_usd_minor,
        sum(processing_cost_usd_minor) as processing_cost_usd_minor,
        sum(realized_fx_loss_usd_minor) as realized_fx_loss_usd_minor,
        sum(contribution_usd_minor) as contribution_usd_minor,
        sum(refunded_usd_minor) as refunded_usd_minor,
        sum(dispute_loss_usd_minor) as dispute_loss_usd_minor
    from {{ ref('mrt_unit_economics_monthly') }}
    group by order_month
)

select
    *,
    safe_divide(net_revenue_usd_minor, gmv_usd_minor) as take_rate,
    safe_divide(contribution_usd_minor, net_revenue_usd_minor) as contribution_margin,
    safe_divide(refunded_orders, orders) as refund_rate,
    safe_divide(disputes, orders) as dispute_rate_count,
    safe_divide(dispute_loss_usd_minor, gmv_usd_minor) as chargeback_loss_rate
from totals
