{#
  Unit economics by month and every slice the dashboards filter on: processor,
  buyer region, seller plan and seller cohort. Every column is additive, so any
  roll-up is a SUM; ratios (take rate, margin, refund and dispute rates) are
  computed from the sums, never averaged. Definitions live in METRICS.md and
  the semantic layer (_metrics.yml).
#}
select
    processor,
    buyer_region,
    plan_name,
    seller_cohort_month,
    date_trunc(order_date, month) as order_month,
    count(*) as orders,
    countif(is_refunded) as refunded_orders,
    sum(dispute_count) as disputes,
    sum(gmv_usd_minor) as gmv_usd_minor,
    sum(platform_fee_revenue_usd_minor) as net_revenue_usd_minor,
    sum(processing_fee_usd_minor) as processing_cost_usd_minor,
    sum(realized_fx_loss_usd_minor) as realized_fx_loss_usd_minor,
    sum(contribution_usd_minor) as contribution_usd_minor,
    sum(refunded_usd_minor) as refunded_usd_minor,
    sum(dispute_loss_to_seller_usd_minor) as dispute_loss_usd_minor
from {{ ref('fct_orders') }}
group by order_month, processor, buyer_region, plan_name, seller_cohort_month
