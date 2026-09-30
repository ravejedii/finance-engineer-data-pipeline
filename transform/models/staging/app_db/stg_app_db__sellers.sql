select
    cast(seller_id as int64) as seller_id,
    name as seller_name,
    country as seller_country,
    cast(plan_id as int64) as current_plan_id,
    status,
    timestamp(created_at) as created_at,
    timestamp(nullif(churned_at, '')) as churned_at
from {{ source('raw', 'app_db_sellers') }}
