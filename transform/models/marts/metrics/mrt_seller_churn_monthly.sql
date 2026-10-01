{#
  Seller churn by month: one row per complete month after the first.

  Arrivals into the month and departures from the month before must account
  for every seller (tested in assert_seller_churn_flows_balance):
    active_sellers   = new + first_seen + retained + reactivated
    sellers_at_start = retained + paused + churned + undetermined

  Churn rates are null while any departure is still undetermined, because a
  partial count would understate churn in the most recent months.
#}
with seller_months as (
    select * from {{ ref('mrt_seller_months') }}
),

months as (
    select activity_month
    from (
        select
            min(activity_month) as first_month,
            max(activity_month) as last_month
        from seller_months
    ) as bounds
    cross join unnest(generate_date_array(
        date_add(bounds.first_month, interval 1 month), bounds.last_month, interval 1 month
    )) as activity_month
),

arrivals as (
    select
        activity_month,
        count(*) as active_sellers,
        countif(arrival = 'new') as new_sellers,
        countif(arrival = 'first_seen') as first_seen_sellers,
        countif(arrival = 'retained') as retained_sellers,
        countif(arrival = 'reactivated') as reactivated_sellers
    from seller_months
    group by activity_month
),

departures as (
    select
        departure_month as activity_month,
        count(*) as sellers_at_start,
        countif(departure = 'retained') as retained_from_start,
        countif(departure = 'paused') as paused_sellers,
        countif(departure = 'churned') as churned_sellers,
        countif(departure = 'undetermined') as undetermined_sellers,
        sum(net_revenue_usd_minor) as start_net_revenue_usd_minor,
        sum(if(departure = 'churned', net_revenue_usd_minor, 0))
            as churned_net_revenue_usd_minor
    from seller_months
    group by departure_month
),

closures as (
    select
        date_trunc(date(churned_at), month) as activity_month,
        count(*) as account_closures
    from {{ ref('stg_app_db__sellers') }}
    where churned_at is not null
    group by activity_month
)

select
    months.activity_month,
    coalesce(departures.sellers_at_start, 0) as sellers_at_start,
    coalesce(arrivals.active_sellers, 0) as active_sellers,
    coalesce(arrivals.new_sellers, 0) as new_sellers,
    coalesce(arrivals.first_seen_sellers, 0) as first_seen_sellers,
    coalesce(arrivals.retained_sellers, 0) as retained_sellers,
    coalesce(arrivals.reactivated_sellers, 0) as reactivated_sellers,
    coalesce(departures.retained_from_start, 0) as retained_from_start,
    coalesce(departures.paused_sellers, 0) as paused_sellers,
    coalesce(departures.churned_sellers, 0) as churned_sellers,
    coalesce(departures.undetermined_sellers, 0) as undetermined_sellers,
    coalesce(departures.start_net_revenue_usd_minor, 0) as start_net_revenue_usd_minor,
    coalesce(departures.churned_net_revenue_usd_minor, 0) as churned_net_revenue_usd_minor,
    coalesce(closures.account_closures, 0) as account_closures,
    if(
        coalesce(departures.undetermined_sellers, 0) > 0,
        null,
        safe_divide(departures.churned_sellers, departures.sellers_at_start)
    ) as seller_churn_rate,
    if(
        coalesce(departures.undetermined_sellers, 0) > 0,
        null,
        safe_divide(
            departures.churned_net_revenue_usd_minor, departures.start_net_revenue_usd_minor
        )
    ) as revenue_churn_rate
from months
left join arrivals
    on months.activity_month = arrivals.activity_month
left join departures
    on months.activity_month = departures.activity_month
left join closures
    on months.activity_month = closures.activity_month
