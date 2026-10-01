{# Rows where a reconciliation difference exceeds the tolerance (minor units). #}
{% test within_tolerance(model, column_name, tolerance) %}
    select *
    from {{ model }}
    where abs({{ column_name }}) > {{ tolerance }}
{% endtest %}
