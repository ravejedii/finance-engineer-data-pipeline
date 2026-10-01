-- Every distinct processor A balance transaction in raw ends up exactly once:
-- in staging (deduplicated, latest restatement) or in stg_exceptions.
with raw_ids as (
    select distinct balance_transaction_id as record_key
    from {{ source('raw', 'processor_a_balance_transactions') }}
    where balance_transaction_id != ''
),

landed as (
    select balance_transaction_id as record_key
    from {{ ref('stg_processor_a__balance_transactions') }}

    union all

    select record_key
    from {{ ref('stg_exceptions') }}
    where source_table = 'processor_a_balance_transactions'
),

landed_counts as (
    select
        record_key,
        count(*) as times_landed
    from landed
    group by record_key
)

select
    raw_ids.record_key,
    coalesce(landed_counts.times_landed, 0) as times_landed
from raw_ids
left join landed_counts
    on raw_ids.record_key = landed_counts.record_key
where coalesce(landed_counts.times_landed, 0) != 1
