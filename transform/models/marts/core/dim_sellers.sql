-- Seller dimension, type 2: one row per seller per plan period.
-- Built from Kiln's plan-change history (effective-dated), not a dbt snapshot:
-- a snapshot only records changes it happens to observe between runs, so a
-- fresh warehouse would have no history at all.
with changes as (
    select * from {{ ref('stg_app_db__seller_plan_changes') }}
),

plans as (
    select distinct
        plan_id,
        plan_name,
        fee_bps
    from {{ ref('stg_app_db__plans') }}
),

sellers as (
    select * from {{ ref('stg_app_db__sellers') }}
),

versions as (
    select
        seller_id,
        plan_id,
        effective_from as valid_from,
        lead(effective_from) over (partition by seller_id order by effective_from) as valid_to
    from changes
)

select
    versions.seller_id,
    sellers.seller_name,
    sellers.seller_country,
    sellers.status as seller_status,
    sellers.created_at as seller_created_at,
    sellers.churned_at as seller_churned_at,
    versions.plan_id,
    plans.plan_name,
    plans.fee_bps,
    versions.valid_from,
    versions.valid_to,
    to_hex(
        md5(concat(cast(versions.seller_id as string), '|', cast(versions.valid_from as string)))
    )
        as seller_version_key,
    versions.valid_to is null as is_current
from versions
inner join sellers
    on versions.seller_id = sellers.seller_id
inner join plans
    on versions.plan_id = plans.plan_id
