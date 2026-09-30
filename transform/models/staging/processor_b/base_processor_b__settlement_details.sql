-- Every raw settlement line, typed, with the reason it is invalid (null when valid).
-- Creation Date is local Amsterdam time; the TimeZone column (CET/CEST) gives the
-- exact UTC offset, which also settles the ambiguous hour when clocks go back.
with source as (
    select * from {{ source('raw', 'processor_b_settlement_details') }}
),

parsed as (
    select
        nullif(psp_reference, '') as psp_reference,
        nullif(merchant_reference, '') as merchant_reference,
        safe_cast(regexp_extract(merchant_reference, r'^KILN-(\d+)$') as int64) as kiln_order_id,
        nullif(payment_method, '') as payment_method,
        creation_date as created_at_local,
        timezone as source_timezone,
        safe.parse_timestamp(
            '%Y-%m-%d %H:%M:%S%Ez',
            creation_date || case timezone when 'CET' then '+01:00' when 'CEST' then '+02:00' end
        ) as created_at,
        type as record_type,
        modification_reference,
        upper(gross_currency) as gross_currency,
        coalesce({{ to_minor_units('nullif(gross_credit_gc, \'\')', 'gross_currency') }}, 0)
        - coalesce({{ to_minor_units('nullif(gross_debit_gc, \'\')', 'gross_currency') }}, 0)
            as gross_minor,
        safe_cast(nullif(exchange_rate, '') as numeric) as exchange_rate,
        upper(net_currency) as net_currency,
        {{ to_minor_units('nullif(net_credit_nc, \'\')', 'net_currency') }} as net_credit_minor,
        {{ to_minor_units('nullif(net_debit_nc, \'\')', 'net_currency') }} as net_debit_minor,
        net_credit_nc,
        net_debit_nc,
        coalesce({{ to_minor_units('nullif(commission_nc, \'\')', 'net_currency') }}, 0)
            as commission_minor,
        coalesce({{ to_minor_units('nullif(markup_nc, \'\')', 'net_currency') }}, 0)
            as markup_minor,
        coalesce({{ to_minor_units('nullif(scheme_fees_nc, \'\')', 'net_currency') }}, 0)
            as scheme_fees_minor,
        coalesce({{ to_minor_units('nullif(interchange_nc, \'\')', 'net_currency') }}, 0)
            as interchange_minor,
        safe_cast(batch_number as int64) as batch_number,
        _source_file,
        _source_line,
        _loaded_at
    from source
)

select
    * except (net_credit_minor, net_debit_minor, net_credit_nc, net_debit_nc),
    coalesce(net_credit_minor, 0) - coalesce(net_debit_minor, 0) as net_minor,
    to_hex(md5(concat(
        coalesce(psp_reference, ''), '|', record_type, '|', modification_reference
    ))) as settlement_line_id,
    case
        when created_at is null then 'unparseable Creation Date / TimeZone'
        when batch_number is null then 'unparseable Batch Number'
        when net_credit_nc != '' and net_credit_minor is null then 'unparseable Net Credit (NC)'
        when net_debit_nc != '' and net_debit_minor is null then 'unparseable Net Debit (NC)'
        when net_credit_nc = '' and net_debit_nc = '' then 'no net amount'
    end as invalid_reason
from parsed
