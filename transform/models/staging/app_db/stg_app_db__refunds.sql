select
    cast(refund_id as int64) as refund_id,
    cast(order_id as int64) as order_id,
    cast(amount_minor as int64) as amount_minor,
    currency,
    processor_refund_ref,
    timestamp(created_at) as created_at
from {{ source('raw', 'app_db_refunds') }}
