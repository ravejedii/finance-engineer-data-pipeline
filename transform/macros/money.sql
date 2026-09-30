{#
  Decimal strings from processor reports -> integer minor units.
  Returns null (never a guess) when the string doesn't have exactly the
  currency's number of decimals: '12.34' USD -> 1234, '3736' JPY -> 3736,
  'N/A' -> null, '12,34' -> null, '12.3' -> null.
  NUMERIC, not FLOAT64: decimal math is exact.
#}
{% macro to_minor_units(amount, currency) -%}
    case
        when upper({{ currency }}) = 'JPY'
            then if(regexp_contains({{ amount }}, r'^-?\d+$'), cast({{ amount }} as int64), null)
        else if(
            regexp_contains({{ amount }}, r'^-?\d+\.\d{2}$'),
            cast(cast({{ amount }} as numeric) * 100 as int64),
            null
        )
    end
{%- endmacro %}

{# Business date encoded in a raw file name, e.g. processor_a/balance_transactions_2025-07-10.csv #}
{% macro file_date(source_file) -%}
    safe.parse_date('%Y-%m-%d', regexp_extract({{ source_file }}, r'(\d{4}-\d{2}-\d{2})'))
{%- endmacro %}
