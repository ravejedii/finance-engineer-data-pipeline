-- One row per calendar day and currency: Kiln's booking rate, the latest
-- published rate on or before that day (weekends carry Friday's rate). Days
-- before the first publication use the first published rate. USD is 1.
with published as (
    select * from {{ ref('stg_fx__reference_rates') }}
),

bounds as (
    select
        min(rate_date) as first_date,
        date_add(max(rate_date), interval 7 day) as last_date
    from published
),

spine as (
    select
        currencies.currency,
        day as rate_date
    from bounds
    cross join unnest(generate_date_array(date_sub(bounds.first_date, interval 7 day), bounds.last_date)) as day
    cross join (select distinct currency from published) as currencies
),

filled as (
    select
        spine.currency,
        spine.rate_date,
        published.rate_date as published_rate_date,
        coalesce(
            last_value(published.units_per_usd ignore nulls) over forward_fill,
            first_value(published.units_per_usd ignore nulls) over back_fill
        ) as units_per_usd
    from spine
    left join published
        on spine.currency = published.currency
        and spine.rate_date = published.rate_date
    window
        forward_fill as (
            partition by spine.currency order by spine.rate_date
            rows between unbounded preceding and current row
        ),
        back_fill as (
            partition by spine.currency order by spine.rate_date
            rows between current row and unbounded following
        )
)

select
    currency,
    rate_date,
    units_per_usd,
    published_rate_date is not null as is_published
from filled

union all

select
    'USD' as currency,
    day as rate_date,
    cast(1 as numeric) as units_per_usd,
    true as is_published
from bounds
cross join unnest(generate_date_array(date_sub(bounds.first_date, interval 7 day), bounds.last_date)) as day
