select
    bank_transaction_id,
    account_number,
    safe.parse_date('%Y-%m-%d', booking_date) as booking_date,
    safe.parse_date('%Y-%m-%d', value_date) as value_date,
    {{ to_minor_units('amount', 'currency') }} as amount_minor,
    upper(currency) as currency,
    counterparty_name,
    end_to_end_reference,
    description,
    _source_file,
    _source_line
from {{ source('raw', 'bank_statements') }}
qualify row_number() over (partition by bank_transaction_id order by _source_file) = 1
