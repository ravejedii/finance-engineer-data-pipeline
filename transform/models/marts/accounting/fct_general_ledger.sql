-- The complete general ledger: every transaction posting plus the month-end
-- close entries (FX remeasurement, bad-debt allowance). This is what the trial
-- balance and financial statements read.
{{ config(
    partition_by={'field': 'posting_date', 'data_type': 'date', 'granularity': 'month'},
    cluster_by=['account_code']
) }}

select
    ledger_line_id,
    journal_entry_id,
    posting_date,
    occurred_at,
    account_code,
    amount_usd_minor,
    source_system,
    source_event_type,
    source_reference,
    processor_reference,
    kiln_order_id,
    seller_id,
    original_currency,
    original_amount_minor
from {{ ref('fct_ledger_entries') }}

union all

select
    ledger_line_id,
    journal_entry_id,
    posting_date,
    occurred_at,
    account_code,
    amount_usd_minor,
    source_system,
    source_event_type,
    source_reference,
    processor_reference,
    kiln_order_id,
    seller_id,
    original_currency,
    original_amount_minor
from {{ ref('int_journal_lines__close') }}
