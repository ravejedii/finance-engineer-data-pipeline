{# Passes when the model has no rows. Used for detection tables, at warn severity. #}
{% test is_empty(model) %}
    select * from {{ model }}
{% endtest %}
