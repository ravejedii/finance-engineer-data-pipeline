-- Processor B numbers its daily settlement files consecutively. A number with
-- no file in the load manifest is a file that never arrived. (Gaps are found
-- from files received, not from rows: a quiet day arrives as a header-only
-- file, which has no rows but is not missing.)
with received as (
    select distinct
        safe_cast(
            regexp_extract(file_name, r'settlement_detail_report_batch_(\d+)\.csv$') as int64
        ) as batch_number
    from {{ source('raw', 'load_manifest') }}
    where source = 'processor_b_settlement_details'
),

bounds as (
    select
        min(batch_number) as first_batch,
        max(batch_number) as last_batch
    from received
),

expected as (
    select batch_number
    from bounds
    cross join unnest(generate_array(bounds.first_batch, bounds.last_batch)) as batch_number
)

select
    expected.batch_number as missing_batch_number,
    concat('PAYOUT-', format('%04d', expected.batch_number)) as expected_payout_reference
from expected
left join received
    on expected.batch_number = received.batch_number
where received.batch_number is null
