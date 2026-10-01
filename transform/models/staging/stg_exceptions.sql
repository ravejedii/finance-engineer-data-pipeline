-- Rows that arrived but can't be trusted, with the reason. Together with the
-- loader's load_exceptions, this is where a raw row goes if it doesn't reach
-- the ledger: nothing disappears silently.
select
    'processor_a_balance_transactions' as source_table,
    coalesce(balance_transaction_id, source_id) as record_key,
    invalid_reason,
    _source_file,
    _source_line
from {{ ref('base_processor_a__balance_transactions') }}
where invalid_reason is not null

union all

select
    'processor_b_settlement_details' as source_table,
    settlement_line_id as record_key,
    invalid_reason,
    _source_file,
    _source_line
from {{ ref('base_processor_b__settlement_details') }}
where invalid_reason is not null

union all

select
    'loader' as source_table,
    cast(line_number as string) as record_key,
    reason as invalid_reason,
    _source_file,
    line_number as _source_line
from {{ source('raw', 'load_exceptions') }}
