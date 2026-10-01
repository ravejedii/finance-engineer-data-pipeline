select
    cast(seller_id as int64) as seller_id,
    cast(plan_id as int64) as plan_id,
    timestamp(effective_from) as effective_from
from {{ source('raw', 'app_db_seller_plan_changes') }}
