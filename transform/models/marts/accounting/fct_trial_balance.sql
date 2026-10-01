-- Monthly trial balance: one row per account per month-end, including accounts
-- with no activity that month (their balance carries forward).
-- closing_balance_usd_minor is debit-positive; across all accounts it nets to
-- zero every month (tested).
with accounts as (
    select * from {{ ref('chart_of_accounts') }}
),

activity as (
    select
        account_code,
        last_day(posting_date) as month_end,
        sum(greatest(amount_usd_minor, 0)) as period_debit_usd_minor,
        sum(greatest(-amount_usd_minor, 0)) as period_credit_usd_minor
    from {{ ref('fct_general_ledger') }}
    group by account_code, month_end
),

grid as (
    select
        month_ends.month_end,
        accounts.account_code,
        accounts.account_name,
        accounts.account_type,
        accounts.normal_balance,
        coalesce(activity.period_debit_usd_minor, 0) as period_debit_usd_minor,
        coalesce(activity.period_credit_usd_minor, 0) as period_credit_usd_minor
    from {{ ref('int_month_ends') }} as month_ends
    cross join accounts
    left join activity
        on
            month_ends.month_end = activity.month_end
            and accounts.account_code = activity.account_code
)

select
    month_end,
    account_code,
    account_name,
    account_type,
    normal_balance,
    period_debit_usd_minor,
    period_credit_usd_minor,
    period_debit_usd_minor - period_credit_usd_minor as period_net_usd_minor,
    sum(period_debit_usd_minor - period_credit_usd_minor) over (
        partition by account_code order by month_end
        rows between unbounded preceding and current row
    ) as closing_balance_usd_minor
from grid
