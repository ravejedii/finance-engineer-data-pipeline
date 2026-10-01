{#
  Seller lifetime value by joining cohort: cumulative contribution per seller
  who joined, month by month since joining. Every seller who joined is in the
  denominator, including those who never sold, matching seller retention.

  Only cohorts that joined inside the order window are included; older cohorts
  are missing their early months, so their cumulative value would be too low.
  LTV here is contribution (revenue - processing cost - realized FX), before
  bad debt, and is observed to date, not projected.
#}
with seller_months as (
    select * from {{ ref('mrt_seller_months') }}
),

bounds as (
    select
        min(activity_month) as first_month,
        max(activity_month) as last_month
    from seller_months
),

cohorts as (
    select
        date_trunc(date(created_at), month) as cohort_month,
        count(*) as cohort_size
    from {{ ref('stg_app_db__sellers') }}
    group by cohort_month
),

grid as (
    select
        cohorts.cohort_month,
        cohorts.cohort_size,
        activity_month
    from cohorts
    cross join bounds
    cross join unnest(generate_date_array(
        cohorts.cohort_month, bounds.last_month, interval 1 month
    )) as activity_month
    where cohorts.cohort_month >= bounds.first_month
),

monthly as (
    select
        cohort_month,
        activity_month,
        sum(net_revenue_usd_minor) as net_revenue_usd_minor,
        sum(contribution_usd_minor) as contribution_usd_minor
    from seller_months
    group by cohort_month, activity_month
),

cumulative as (
    select
        grid.cohort_month,
        grid.activity_month,
        grid.cohort_size,
        coalesce(monthly.net_revenue_usd_minor, 0) as net_revenue_usd_minor,
        coalesce(monthly.contribution_usd_minor, 0) as contribution_usd_minor,
        date_diff(grid.activity_month, grid.cohort_month, month) as months_since_join,
        sum(coalesce(monthly.net_revenue_usd_minor, 0)) over (
            partition by grid.cohort_month order by grid.activity_month
        ) as cumulative_net_revenue_usd_minor,
        sum(coalesce(monthly.contribution_usd_minor, 0)) over (
            partition by grid.cohort_month order by grid.activity_month
        ) as cumulative_contribution_usd_minor
    from grid
    left join monthly
        on
            grid.cohort_month = monthly.cohort_month
            and grid.activity_month = monthly.activity_month
)

select
    *,
    safe_divide(cumulative_contribution_usd_minor, cohort_size) as ltv_per_seller_usd_minor
from cumulative
