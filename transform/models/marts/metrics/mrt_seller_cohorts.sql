{#
  Seller cohort retention and net revenue retention.
  Cohort = the month a seller joined Kiln. For each cohort and each month
  since joining:
    seller_retention  = sellers with at least one order that month / cohort size
    revenue_retention = cohort's platform fee revenue that month / its revenue
                        in month 0 (its first month). Above 1.0 means the
                        surviving sellers grew faster than churn removed revenue.
#}
with sellers as (
    select
        seller_id,
        date_trunc(date(created_at), month) as cohort_month
    from {{ ref('stg_app_db__sellers') }}
),

cohort_sizes as (
    select
        cohort_month,
        count(*) as cohort_size
    from sellers
    group by cohort_month
),

activity as (
    select
        sellers.cohort_month,
        date_trunc(orders.order_date, month) as activity_month,
        count(distinct orders.seller_id) as active_sellers,
        sum(orders.platform_fee_revenue_usd_minor) as net_revenue_usd_minor
    from {{ ref('fct_orders') }} as orders
    inner join sellers
        on orders.seller_id = sellers.seller_id
    group by sellers.cohort_month, activity_month
),

with_base as (
    select
        activity.cohort_month,
        activity.activity_month,
        cohort_sizes.cohort_size,
        activity.active_sellers,
        activity.net_revenue_usd_minor,
        date_diff(activity.activity_month, activity.cohort_month, month) as months_since_join,
        -- Null when the cohort's month 0 is before the data window (no baseline).
        max(if(
            activity.activity_month = activity.cohort_month,
            activity.net_revenue_usd_minor, null)) over (
            partition by activity.cohort_month
        ) as month_zero_revenue_usd_minor
    from activity
    inner join cohort_sizes
        on activity.cohort_month = cohort_sizes.cohort_month
    where activity.activity_month >= activity.cohort_month
)

select
    *,
    safe_divide(active_sellers, cohort_size) as seller_retention,
    safe_divide(net_revenue_usd_minor, month_zero_revenue_usd_minor) as revenue_retention
from with_base
