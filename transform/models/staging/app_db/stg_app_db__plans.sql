-- One row per plan and presentment currency: the percentage fee plus that
-- currency's fixed fee.
with plans as (
    select
        cast(plan_id as int64) as plan_id,
        name as plan_name,
        cast(fee_bps as int64) as fee_bps
    from {{ source('raw', 'app_db_plans') }}
),

fees as (
    select
        currency,
        cast(plan_id as int64) as plan_id,
        cast(fixed_fee_minor as int64) as fixed_fee_minor
    from {{ source('raw', 'app_db_plan_fixed_fees') }}
)

select
    plans.plan_id,
    plans.plan_name,
    plans.fee_bps,
    fees.currency,
    fees.fixed_fee_minor
from plans
inner join fees
    on plans.plan_id = fees.plan_id
