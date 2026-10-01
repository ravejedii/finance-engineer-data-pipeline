{#
  The general ledger: one row per journal line, USD minor units.

  Incremental by insert_overwrite on monthly partitions. Each run recomputes
  every month the lookback window touches and replaces those partitions whole.
  Compared with merge on ledger_line_id, a whole-partition replace also removes
  lines that no longer exist upstream (for example after a restated row changes
  an entry's shape), so the ledger can't keep stale lines.
#}
{{ config(
    materialized='incremental',
    incremental_strategy='insert_overwrite',
    partition_by={'field': 'posting_date', 'data_type': 'date', 'granularity': 'month'},
    cluster_by=['account_code', 'source_system'],
    on_schema_change='fail'
) }}

with lines as (
    {% for model in ['int_journal_lines__settlements', 'int_journal_lines__cash'] %}
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
        from {{ ref(model) }}
        {% if not loop.last %}union all{% endif %}
    {% endfor %}
)

select
    lines.*,
    greatest(lines.amount_usd_minor, 0) as debit_usd_minor,
    greatest(-lines.amount_usd_minor, 0) as credit_usd_minor
from lines
{% if is_incremental() %}
    where lines.posting_date >= date_trunc(
        date_sub(
            (select max(existing.posting_date) from {{ this }} as existing),
            interval {{ var('ledger_lookback_days') }} day
        ),
        month
    )
{% endif %}
