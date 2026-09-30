-- One row per payout, at its latest known status (the raw file is a status log).
with source as (
    select * from {{ source('raw', 'processor_a_payouts') }}
),

parsed as (
    select
        payout_id,
        safe.parse_timestamp('%Y-%m-%d %H:%M:%S', created_utc, 'UTC') as created_at,
        safe.parse_date('%Y-%m-%d', arrival_date) as arrival_date,
        {{ to_minor_units('amount', 'currency') }} as amount_minor,
        upper(currency) as currency,
        status,
        nullif(failure_code, '') as failure_code,
        safe.parse_timestamp('%Y-%m-%d %H:%M:%S', status_changed_utc, 'UTC') as status_changed_at,
        _source_file,
        _source_line
    from source
)

select *
from parsed
qualify row_number() over (
    partition by payout_id
    order by status_changed_at desc, _source_file desc
) = 1
