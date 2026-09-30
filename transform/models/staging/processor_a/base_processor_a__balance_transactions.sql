-- Every raw row, typed, with the reason it is invalid (null when valid).
-- Staging reads the valid rows; stg_exceptions reads the rest.
with source as (
    select * from {{ source('raw', 'processor_a_balance_transactions') }}
),

parsed as (
    select
        nullif(balance_transaction_id, '') as balance_transaction_id,
        safe.parse_timestamp('%Y-%m-%d %H:%M:%S', created_utc, 'UTC') as created_at,
        created_utc as created_at_source,
        'UTC' as source_timezone,
        safe.parse_date('%Y-%m-%d', substr(available_on_utc, 1, 10)) as available_on_date,
        upper(currency) as settlement_currency,
        {{ to_minor_units('gross', 'currency') }} as gross_minor,
        {{ to_minor_units('fee', 'currency') }} as fee_minor,
        {{ to_minor_units('net', 'currency') }} as net_minor,
        reporting_category,
        nullif(source_id, '') as source_id,
        description,
        upper(nullif(customer_facing_currency, '')) as presentment_currency,
        {{ to_minor_units('customer_facing_amount', 'customer_facing_currency') }}
            as presentment_amount_minor,
        nullif(automatic_payout_id, '') as automatic_payout_id,
        safe_cast(nullif(payment_metadata_kiln_order_id, '') as int64) as kiln_order_id,
        _source_file,
        _source_line,
        _loaded_at,
        {{ file_date('_source_file') }} as file_date
    from source
)

select
    *,
    case
        when balance_transaction_id is null then 'missing balance_transaction_id'
        when created_at is null then 'unparseable created_utc'
        when gross_minor is null then 'unparseable gross'
        when fee_minor is null then 'unparseable fee'
        when net_minor is null then 'unparseable net'
        when gross_minor - fee_minor != net_minor then 'gross - fee != net'
    end as invalid_reason
from parsed
