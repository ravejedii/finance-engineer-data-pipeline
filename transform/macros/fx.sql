{#
  Minor units of `currency` -> USD minor units, given the rate quoted as units
  of currency per 1 USD. Division in BIGNUMERIC, banker's rounding once at the
  end, same as the generator's truth.
#}
{% macro to_usd_minor(amount_minor, currency, units_per_usd) -%}
    case
        when {{ currency }} = 'USD' then {{ amount_minor }}
        when {{ currency }} = 'JPY' then cast(round(
            cast({{ amount_minor }} as bignumeric) * 100 / {{ units_per_usd }}, 0, 'ROUND_HALF_EVEN'
        ) as int64)
        else cast(round(
            cast({{ amount_minor }} as bignumeric) / {{ units_per_usd }}, 0, 'ROUND_HALF_EVEN'
        ) as int64)
    end
{%- endmacro %}
