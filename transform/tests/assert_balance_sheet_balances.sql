-- Assets = liabilities + equity at every month-end. Returns months that don't.
select
    month_end,
    sum(if(section = 'asset', amount_usd_minor, -amount_usd_minor)) as imbalance_usd_minor
from {{ ref('rpt_close_package') }}
where statement = 'balance_sheet'
group by month_end
having imbalance_usd_minor != 0
