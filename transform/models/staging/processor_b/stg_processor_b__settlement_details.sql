-- One row per settlement line. An exact duplicate re-sent in a later batch file
-- keeps its original Batch Number, so keeping the earliest file removes it; a
-- late row carries the later Batch Number and settles in that batch.
select * except (invalid_reason)
from {{ ref('base_processor_b__settlement_details') }}
where invalid_reason is null
qualify row_number() over (
    partition by settlement_line_id
    order by _source_file, _source_line
) = 1
