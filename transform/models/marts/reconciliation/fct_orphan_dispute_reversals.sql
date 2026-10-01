{#
  Orders where a processor reported a won chargeback (dispute_reversal) but
  never reported the chargeback itself. The ledger then credits the seller
  money it never saw leave, so the order's dispute loss is negative.
  The usual cause is a settlement file that never arrived (see
  fct_processor_b_batch_gaps). A row here is a source-data break to chase
  with the processor, not something the ledger can fix.
#}
select
    kiln_order_id,
    source_system as processor,
    -- Count events (journal entries), not lines: zero-amount lines are dropped,
    -- so a dispute and its reversal can have different line counts.
    count(distinct if(source_event_type = 'dispute', journal_entry_id, null))
        as disputes_reported,
    count(distinct if(source_event_type = 'dispute_reversal', journal_entry_id, null))
        as reversals_reported,
    min(if(source_event_type = 'dispute_reversal', posting_date, null)) as first_reversal_date,
    sum(if(
        account_code = '2000' and source_event_type = 'dispute_reversal', -amount_usd_minor, 0
    )) as reversal_credited_to_seller_usd_minor
from {{ ref('fct_ledger_entries') }}
where
    kiln_order_id is not null
    and source_event_type in ('dispute', 'dispute_reversal')
group by kiln_order_id, source_system
having reversals_reported > disputes_reported
