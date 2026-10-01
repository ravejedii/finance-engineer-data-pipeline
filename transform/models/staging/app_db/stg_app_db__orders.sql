select
    cast(order_id as int64) as order_id,
    cast(seller_id as int64) as seller_id,
    cast(product_id as int64) as product_id,
    cast(buyer_id as int64) as buyer_id,
    buyer_country,
    currency,
    cast(amount_minor as int64) as amount_minor,
    cast(platform_fee_minor as int64) as platform_fee_minor,
    cast(plan_id as int64) as plan_id,
    processor,
    processor_charge_ref,
    order_type,
    status,
    timestamp(created_at) as created_at
from {{ source('raw', 'app_db_orders') }}
