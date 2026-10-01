{#
  One row per seller per month-end: what Kiln owes the seller (or the seller
  owes Kiln), and for how long the balance has been negative.

  A negative payable is a receivable from the seller, typically from a refund
  or lost dispute that landed after the seller was paid. Once it has stayed
  negative for at least var('bad_debt_aging_days') days at a month-end, the
  whole receivable is reserved. If the seller's balance recovers, the reserve
  goes back to zero, and the close entry reverses it.
#}
with movements as (
    select
        seller_id,
        posting_date,
        -- Seller payable is a credit balance: credits increase what Kiln owes.
        sum(-amount_usd_minor) as payable_change_usd_minor
    from {{ ref('fct_ledger_entries') }}
    where account_code = '2000' and seller_id is not null
    group by seller_id, posting_date
),

running as (
    select
        seller_id,
        posting_date,
        sum(payable_change_usd_minor) over (
            partition by seller_id order by posting_date
        ) as payable_balance_usd_minor
    from movements
),

streak_starts as (
    select
        seller_id,
        posting_date,
        payable_balance_usd_minor,
        if(
            payable_balance_usd_minor < 0
            and coalesce(
                lag(payable_balance_usd_minor) over (
                    partition by seller_id order by posting_date
                ),
                0
            ) >= 0,
            posting_date,
            null
        ) as streak_start
    from running
),

with_negative_since as (
    select
        seller_id,
        posting_date,
        payable_balance_usd_minor,
        if(
            payable_balance_usd_minor < 0,
            last_value(streak_start ignore nulls) over (
                partition by seller_id order by posting_date
                rows between unbounded preceding and current row
            ),
            null
        ) as negative_since
    from streak_starts
),

snapshots as (
    select
        month_ends.month_end,
        balances.seller_id,
        balances.payable_balance_usd_minor,
        balances.negative_since
    from {{ ref('int_month_ends') }} as month_ends
    inner join with_negative_since as balances
        on month_ends.month_end >= balances.posting_date
    qualify row_number() over (
        partition by month_ends.month_end, balances.seller_id
        order by balances.posting_date desc
    ) = 1
)

select
    month_end,
    seller_id,
    payable_balance_usd_minor,
    negative_since,
    greatest(-payable_balance_usd_minor, 0) as receivable_usd_minor,
    date_diff(month_end, negative_since, day) as days_negative,
    coalesce(
        date_diff(month_end, negative_since, day) >= {{ var('bad_debt_aging_days') }}, false
    ) as is_reserved,
    if(
        date_diff(month_end, negative_since, day) >= {{ var('bad_debt_aging_days') }},
        -payable_balance_usd_minor,
        0
    ) as allowance_usd_minor
from snapshots
