-- One row per balance transaction. Duplicates across daily files collapse to one;
-- a restated transaction (same id, corrected values in a later file) keeps the
-- latest file's version.
select * except (invalid_reason)
from {{ ref('base_processor_a__balance_transactions') }}
where invalid_reason is null
qualify row_number() over (
    partition by balance_transaction_id
    order by file_date desc, _source_line desc
) = 1
