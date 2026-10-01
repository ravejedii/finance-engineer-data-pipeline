-- Every settlement event reaches the ledger as exactly one journal entry.
with events as (
    select
        processor,
        settlement_event_id
    from {{ ref('int_settlement_events') }}
),

posted as (
    select
        source_system as processor,
        source_reference as settlement_event_id,
        count(distinct journal_entry_id) as entries
    from {{ ref('fct_ledger_entries') }}
    where source_system in ('processor_a', 'processor_b')
    group by processor, settlement_event_id
)

select
    events.processor,
    events.settlement_event_id,
    coalesce(posted.entries, 0) as entries
from events
left join posted
    on
        events.processor = posted.processor
        and events.settlement_event_id = posted.settlement_event_id
where coalesce(posted.entries, 0) != 1
