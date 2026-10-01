-- Published on business days only, quoted as units of currency per 1 USD.
-- To convert an amount to USD, divide by the rate.
select
    cast(rate as numeric) as units_per_usd,
    upper(quote_currency) as currency,
    safe.parse_date('%Y-%m-%d', rate_date) as rate_date
from {{ source('raw', 'fx_reference_rates') }}
where upper(base_currency) = 'USD'
qualify row_number() over (
    partition by rate_date, quote_currency order by _source_file desc
) = 1
