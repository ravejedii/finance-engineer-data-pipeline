{#
  Double-entry check: within each group, signed amounts (debit +, credit -) net
  to zero. Returns the groups that don't.
#}
{% test sums_to_zero(model, column_name, group_by) %}
    select
        {{ group_by }} as group_key,
        sum({{ column_name }}) as imbalance,
        countif({{ column_name }} is null) as null_lines
    from {{ model }}
    group by group_key
    having sum({{ column_name }}) != 0 or countif({{ column_name }} is null) > 0
{% endtest %}
