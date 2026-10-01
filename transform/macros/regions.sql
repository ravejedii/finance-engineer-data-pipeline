{# Buyer country -> reporting region. One definition, used by every mart. #}
{% macro buyer_region(country) -%}
    case
        when {{ country }} in ('US', 'CA') then 'North America'
        when {{ country }} in ('DE', 'FR', 'NL', 'ES', 'IT') then 'EU'
        when {{ country }} = 'GB' then 'UK'
        when {{ country }} = 'BR' then 'LATAM'
        when {{ country }} = 'JP' then 'APAC'
        else 'Other'
    end
{%- endmacro %}
