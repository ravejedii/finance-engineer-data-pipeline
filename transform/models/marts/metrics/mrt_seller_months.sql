{#
  One row per seller per month in which the seller had at least one order:
  how the seller arrived in that month, and what happened after it.

  Churn is behavioral: a seller churns when three full calendar months pass
  with no orders (about 90 days). The account status in the app database is
  a separate thing (a closure), compared on the churn dashboard.

    arrival    new          first active month; joined inside the order window
               first_seen   first active month; joined before the window, so
                            its earlier history is unknown
               retained     also active the month before
               reactivated  active again after one or more inactive months
    departure  retained     also active the month after
               paused       inactive next month, back within three months
               churned      no orders in the next three months
               undetermined fewer than three complete months of data follow

  A trailing partial month is excluded, so a data cut-off never looks like churn.
#}
with seller_orders as (
    select
        order_id,
        seller_id,
        seller_cohort_month,
        plan_name,
        order_date,
        platform_fee_revenue_usd_minor,
        contribution_usd_minor,
        date_trunc(order_date, month) as activity_month
    from {{ ref('fct_orders') }}
),

window_bounds as (
    select
        min(activity_month) as first_month,
        -- The last month counts only if the data runs to its final day.
        if(
            max(order_date) = last_day(max(order_date), month),
            max(activity_month),
            date_sub(max(activity_month), interval 1 month)
        ) as last_complete_month
    from seller_orders
),

seller_months as (
    select
        seller_orders.seller_id,
        seller_orders.activity_month,
        any_value(seller_orders.seller_cohort_month) as cohort_month,
        -- The plan on the seller's latest order of the month.
        array_agg(
            seller_orders.plan_name
            order by seller_orders.order_date desc, seller_orders.order_id desc
            limit 1
        )[offset(0)] as plan_name,
        count(*) as orders,
        sum(seller_orders.platform_fee_revenue_usd_minor) as net_revenue_usd_minor,
        sum(seller_orders.contribution_usd_minor) as contribution_usd_minor
    from seller_orders
    cross join window_bounds
    where seller_orders.activity_month <= window_bounds.last_complete_month
    group by seller_orders.seller_id, seller_orders.activity_month
),

sequenced as (
    select
        *,
        lag(activity_month) over (
            partition by seller_id order by activity_month
        ) as previous_active_month,
        lead(activity_month) over (
            partition by seller_id order by activity_month
        ) as next_active_month
    from seller_months
)

select
    sequenced.seller_id,
    sequenced.activity_month,
    sequenced.cohort_month,
    sequenced.plan_name,
    sequenced.orders,
    sequenced.net_revenue_usd_minor,
    sequenced.contribution_usd_minor,
    sequenced.previous_active_month,
    sequenced.next_active_month,
    case
        when
            sequenced.previous_active_month
            = date_sub(sequenced.activity_month, interval 1 month)
            then 'retained'
        when sequenced.previous_active_month is not null then 'reactivated'
        when sequenced.cohort_month >= window_bounds.first_month then 'new'
        else 'first_seen'
    end as arrival,
    case
        when
            sequenced.next_active_month
            = date_add(sequenced.activity_month, interval 1 month)
            then 'retained'
        when
            sequenced.next_active_month
            <= date_add(sequenced.activity_month, interval 3 month)
            then 'paused'
        when sequenced.next_active_month is not null then 'churned'
        when
            date_add(sequenced.activity_month, interval 3 month)
            <= window_bounds.last_complete_month
            then 'churned'
        else 'undetermined'
    end as departure,
    -- The month a departure is counted in: the first month without orders.
    date_add(sequenced.activity_month, interval 1 month) as departure_month
from sequenced
cross join window_bounds
