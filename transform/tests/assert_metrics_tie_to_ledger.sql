-- The metric marts must tie to the ledger to the cent. Returns any metric whose
-- total in mrt_metrics_monthly differs from the ledger account it comes from.
-- Revenue ties to the whole of account 4000: it is only ever booked for a real
-- order. Processing cost ties to the 5000 lines of real orders; fees on charges
-- with no Kiln order (suspense) are a cost of no order, so not in the metric.
with ledger as (
    select
        sum(if(entries.account_code = '4000', -entries.amount_usd_minor, 0))
            as net_revenue_usd_minor,
        sum(if(
            entries.account_code = '5000' and orders.order_id is not null,
            entries.amount_usd_minor, 0
        )) as processing_cost_usd_minor
    from {{ ref('fct_ledger_entries') }} as entries
    left join {{ ref('stg_app_db__orders') }} as orders
        on entries.kiln_order_id = orders.order_id
),

metrics as (
    select
        sum(net_revenue_usd_minor) as net_revenue_usd_minor,
        sum(processing_cost_usd_minor) as processing_cost_usd_minor
    from {{ ref('mrt_metrics_monthly') }}
),

unpivoted as (
    select
        'net_revenue' as metric,
        ledger.net_revenue_usd_minor as ledger_usd_minor,
        metrics.net_revenue_usd_minor as metric_usd_minor
    from ledger cross join metrics
    union all
    select
        'processing_cost' as metric,
        ledger.processing_cost_usd_minor as ledger_usd_minor,
        metrics.processing_cost_usd_minor as metric_usd_minor
    from ledger cross join metrics
)

select *
from unpivoted
where ledger_usd_minor != metric_usd_minor
