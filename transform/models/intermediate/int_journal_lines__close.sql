{#
  Month-end close entries, posted on the last day of each month.

  1. FX remeasurement. EUR balances (cash at processor B, EUR in transit, EUR
     bank) are restated to the month-end rate. The entry each month is the
     change in the gap between the target (EUR balance at the month-end rate)
     and the carrying amount the transactions built up, so the gap is always
     fully booked and never booked twice.
       Dr/Cr EUR account     Cr/Dr unrealized FX remeasurement (5210)

  2. Bad-debt allowance. The reserve should equal the total of aged seller
     receivables. Each month books the change:
       increase:  Dr bad debt expense (5300)   Cr allowance (1300)
       decrease:  Dr allowance (1300)          Cr bad debt expense (5300)  (recovery)
#}
with month_ends as (
    select * from {{ ref('int_month_ends') }}
),

rates as (
    select * from {{ ref('int_fx_rates_daily') }}
    where currency = 'EUR'
),

eur_movements as (
    -- Native EUR amounts moving through each EUR account.
    select
        '1010' as account_code,
        event_date as movement_date,
        net_settlement_minor as eur_minor
    from {{ ref('int_settlement_events') }}
    where processor = 'processor_b'

    union all

    select
        '1060' as account_code,
        event_date as movement_date,
        -net_settlement_minor as eur_minor
    from {{ ref('int_settlement_events') }}
    where processor = 'processor_b' and event_type = 'payout'

    union all

    select
        account_code,
        booking_date as movement_date,
        if(account_code = '1060', -amount_minor, amount_minor) as eur_minor
    from {{ ref('stg_bank__statement_lines') }}
    cross join unnest(['1060', '1110']) as account_code
    where currency = 'EUR' and counterparty_name = 'PROCESSOR B SETTLEMENT'
),

eur_accounts as (
    select account_code from unnest(['1010', '1060', '1110']) as account_code
),

eur_by_month as (
    select
        account_code,
        last_day(movement_date) as month_end,
        sum(eur_minor) as eur_minor
    from eur_movements
    group by account_code, month_end
),

usd_by_month as (
    select
        account_code,
        last_day(posting_date) as month_end,
        sum(amount_usd_minor) as usd_minor
    from {{ ref('fct_ledger_entries') }}
    where account_code in ('1010', '1060', '1110')
    group by account_code, month_end
),

carrying as (
    select
        month_ends.month_end,
        eur_accounts.account_code,
        sum(coalesce(eur_by_month.eur_minor, 0)) over cumulative as eur_balance_minor,
        sum(coalesce(usd_by_month.usd_minor, 0)) over cumulative as carrying_usd_minor
    from month_ends
    cross join eur_accounts
    left join eur_by_month
        on
            month_ends.month_end = eur_by_month.month_end
            and eur_accounts.account_code = eur_by_month.account_code
    left join usd_by_month
        on
            month_ends.month_end = usd_by_month.month_end
            and eur_accounts.account_code = usd_by_month.account_code
    window cumulative as (
        partition by eur_accounts.account_code order by month_ends.month_end
        rows between unbounded preceding and current row
    )
),

remeasurement as (
    select
        carrying.month_end,
        carrying.account_code,
        {{ to_usd_minor('carrying.eur_balance_minor', "'EUR'", 'rates.units_per_usd') }}
        - carrying.carrying_usd_minor as gap_usd_minor
    from carrying
    inner join rates
        on carrying.month_end = rates.rate_date
),

remeasurement_entries as (
    select
        month_end,
        account_code,
        gap_usd_minor - coalesce(
            lag(gap_usd_minor) over (partition by account_code order by month_end), 0
        ) as adjustment_usd_minor
    from remeasurement
),

allowance_totals as (
    select
        month_ends.month_end,
        coalesce(sum(aging.allowance_usd_minor), 0) as allowance_usd_minor
    from month_ends
    left join {{ ref('fct_seller_balance_aging') }} as aging
        on month_ends.month_end = aging.month_end
    group by month_ends.month_end
),

allowance_entries as (
    select
        month_end,
        allowance_usd_minor - coalesce(
            lag(allowance_usd_minor) over (order by month_end), 0
        ) as adjustment_usd_minor
    from allowance_totals
),

lines as (
    select
        to_hex(md5(concat(
            'close|fx|', remeasurement_entries.account_code, '|',
            cast(remeasurement_entries.month_end as string)
        )))
            as journal_entry_id,
        remeasurement_entries.month_end as posting_date,
        'fx_remeasurement' as source_event_type,
        line.account_code,
        line.amount_usd_minor
    from remeasurement_entries
    cross join
        unnest([
            struct(
                remeasurement_entries.account_code as account_code,
                remeasurement_entries.adjustment_usd_minor as amount_usd_minor
            ),
            struct('5210', -remeasurement_entries.adjustment_usd_minor)
        ]) as line

    union all

    select
        to_hex(md5(concat('close|bad_debt|', cast(allowance_entries.month_end as string))))
            as journal_entry_id,
        allowance_entries.month_end as posting_date,
        'bad_debt_allowance' as source_event_type,
        line.account_code,
        line.amount_usd_minor
    from allowance_entries
    cross join unnest([
        struct('5300' as account_code, allowance_entries.adjustment_usd_minor as amount_usd_minor),
        struct('1300', -allowance_entries.adjustment_usd_minor)
    ]) as line
)

select
    journal_entry_id,
    posting_date,
    account_code,
    amount_usd_minor,
    'close' as source_system,
    source_event_type,
    cast(posting_date as string) as source_reference,
    cast(null as string) as processor_reference,
    cast(null as int64) as kiln_order_id,
    cast(null as int64) as seller_id,
    'USD' as original_currency,
    amount_usd_minor as original_amount_minor,
    to_hex(md5(concat(journal_entry_id, '|', account_code))) as ledger_line_id,
    timestamp(posting_date) as occurred_at
from lines
where amount_usd_minor != 0
