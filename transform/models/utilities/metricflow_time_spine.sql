-- Daily calendar the dbt semantic layer (MetricFlow) uses for time-based metrics.
{{ config(materialized='table') }}

select date_day
from unnest(generate_date_array(date '2023-01-01', date '2027-12-31')) as date_day
