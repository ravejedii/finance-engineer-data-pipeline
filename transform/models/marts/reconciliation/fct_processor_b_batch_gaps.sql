-- Processor B numbers its daily settlement files consecutively. A gap in the
-- sequence is a file that never arrived. One row per missing batch number.
with batches as (
    select distinct batch_number
    from {{ ref('base_processor_b__settlement_details') }}
    where batch_number is not null
),

bounds as (
    select
        min(batch_number) as first_batch,
        max(batch_number) as last_batch
    from batches
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
left join batches
    on expected.batch_number = batches.batch_number
where batches.batch_number is null
