select
    cast(product_id as int64) as product_id,
    cast(seller_id as int64) as seller_id,
    name as product_name,
    product_type,
    cast(price_usd_minor as int64) as price_usd_minor,
    timestamp(created_at) as created_at
from {{ source('raw', 'app_db_products') }}
