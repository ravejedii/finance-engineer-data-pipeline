-- Every month-end from the first to the last month with ledger activity.
with bounds as (
    select
        date_trunc(min(posting_date), month) as first_month,
        date_trunc(max(posting_date), month) as last_month
    from {{ ref('fct_ledger_entries') }}
)

select last_day(month_start) as month_end
from bounds
cross join unnest(generate_date_array(bounds.first_month, bounds.last_month, interval 1 month))
    as month_start
