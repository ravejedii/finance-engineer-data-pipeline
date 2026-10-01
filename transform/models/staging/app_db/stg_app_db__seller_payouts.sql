select
    cast(payout_id as int64) as seller_payout_id,
    cast(seller_id as int64) as seller_id,
    cast(amount_minor as int64) as amount_minor,
    currency,
    status,
    timestamp(initiated_at) as initiated_at,
    timestamp(nullif(settled_at, '')) as settled_at,
    timestamp(nullif(returned_at, '')) as returned_at
from {{ source('raw', 'app_db_seller_payouts') }}
