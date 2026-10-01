{#
  Drops every dataset this CI run created: the target dataset itself and its
  custom-schema children (<target>_staging, <target>_marts, ...).
  Matches on "<target>_" rather than a bare prefix so that cleaning up
  ci_pr_1 never touches ci_pr_12.
#}
{% macro drop_ci_datasets() %}
    {% if target.name != 'ci' or not target.schema.startswith('ci_') %}
        {{ exceptions.raise_compiler_error(
            "drop_ci_datasets only runs on the ci target with a ci_ dataset; got target="
            ~ target.name ~ ", dataset=" ~ target.schema
        ) }}
    {% endif %}

    {% set all_datasets = adapter.list_schemas(database=target.database) %}
    {% for dataset in all_datasets %}
        {% if dataset == target.schema or dataset.startswith(target.schema ~ '_') %}
            {% do log("Dropping dataset " ~ target.database ~ "." ~ dataset, info=true) %}
            {% set relation = api.Relation.create(database=target.database, schema=dataset) %}
            {% do adapter.drop_schema(relation) %}
        {% endif %}
    {% endfor %}
{% endmacro %}
