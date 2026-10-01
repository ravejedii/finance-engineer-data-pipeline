{#
  Monthly close package in presentation form: income statement for the month
  and balance sheet at month-end, one row per line item, amounts in USD.
  Revenue, assets, liabilities and equity are shown positive; expenses are
  shown positive as costs. Retained earnings are cumulative net income, so the
  balance sheet balances by construction (tested).
#}
with tb as (
    select * from {{ ref('fct_trial_balance') }}
),

income_statement as (
    select
        month_end,
        'income_statement' as statement,
        account_type as section,
        account_name as line_item,
        cast(account_code as int64) as line_order,
        if(account_type = 'revenue', -period_net_usd_minor, period_net_usd_minor)
            as amount_usd_minor
    from tb
    where account_type in ('revenue', 'expense')

    union all

    select
        month_end,
        'income_statement' as statement,
        'total' as section,
        'net_income' as line_item,
        9999 as line_order,
        -sum(period_net_usd_minor) as amount_usd_minor
    from tb
    where account_type in ('revenue', 'expense')
    group by month_end
),

balance_sheet as (
    select
        month_end,
        'balance_sheet' as statement,
        if(account_type = 'contra_asset', 'asset', account_type) as section,
        account_name as line_item,
        cast(account_code as int64) as line_order,
        if(
            account_type in ('asset', 'contra_asset'), closing_balance_usd_minor,
            -closing_balance_usd_minor
        ) as amount_usd_minor
    from tb
    where account_type in ('asset', 'contra_asset', 'liability')

    union all

    select
        month_end,
        'balance_sheet' as statement,
        'equity' as section,
        'retained_earnings' as line_item,
        3000 as line_order,
        -sum(closing_balance_usd_minor) as amount_usd_minor
    from tb
    where account_type in ('revenue', 'expense')
    group by month_end
)

select
    *,
    cast(amount_usd_minor as numeric) / 100 as amount_usd
from income_statement

union all

select
    *,
    cast(amount_usd_minor as numeric) / 100 as amount_usd
from balance_sheet
