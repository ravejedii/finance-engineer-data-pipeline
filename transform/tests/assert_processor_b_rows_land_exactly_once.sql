-- Every distinct processor B settlement line in raw ends up exactly once:
-- in staging or in stg_exceptions.
with raw_keys as (
    select distinct
        to_hex(md5(concat(
            coalesce(nullif(psp_reference, ''), ''), '|', type, '|', modification_reference
        ))) as record_key
    from {{ source('raw', 'processor_b_settlement_details') }}
),

landed as (
    select settlement_line_id as record_key
    from {{ ref('stg_processor_b__settlement_details') }}

    union all

    select record_key
    from {{ ref('stg_exceptions') }}
    where source_table = 'processor_b_settlement_details'
),

landed_counts as (
    select
        record_key,
        count(*) as times_landed
    from landed
    group by record_key
)

select
    raw_keys.record_key,
    coalesce(landed_counts.times_landed, 0) as times_landed
from raw_keys
left join landed_counts
    on raw_keys.record_key = landed_counts.record_key
where coalesce(landed_counts.times_landed, 0) != 1
