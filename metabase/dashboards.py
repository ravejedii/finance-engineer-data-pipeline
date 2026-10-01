"""The Kiln dashboards, as data: each card is a native BigQuery SQL question.

`{m}` is replaced with the fully qualified marts dataset, e.g.
`finance-engineer-data-pipeline`.kiln_marts. Money is stored as integer USD
cents; the SQL divides by 100 only for display.
"""

FINANCE_CLOSE = {
    "name": "Kiln: Finance close",
    "description": (
        "Month-end close: does the ledger balance, do processors, ledger and bank "
        "agree, and what do FX and bad debt cost. Synthetic data."
    ),
    "cards": [
        {
            "name": "Trial balance check (must be 0 every month)",
            "display": "table",
            "size": (8, 6),
            "sql": """
select
    month_end,
    sum(closing_balance_usd_minor) / 100 as trial_balance_net_usd,
    if(sum(closing_balance_usd_minor) = 0, 'balanced', 'OUT OF BALANCE') as status
from {m}.fct_trial_balance
group by month_end
order by month_end desc
""",
        },
        {
            "name": "Income statement, by month",
            "display": "table",
            "size": (16, 6),
            "sql": """
select month_end, section, line_item, amount_usd
from {m}.rpt_close_package
where statement = 'income_statement'
order by month_end desc, line_order
""",
        },
        {
            "name": "Daily three-way reconciliation: days by status",
            "display": "bar",
            "size": (12, 6),
            "settings": {
                "graph.dimensions": ["month", "break_status"],
                "graph.metrics": ["days"],
                "stackable.stack_type": "stacked",
            },
            "sql": """
select date_trunc(recon_date, month) as month, break_status, count(*) as days
from {m}.fct_daily_reconciliation
group by month, break_status
order by month
""",
        },
        {
            "name": "Payout reconciliation (processor vs bank)",
            "display": "table",
            "size": (12, 6),
            "sql": """
select
    recon_status,
    processor,
    currency,
    count(*) as payouts,
    sum(abs(difference_minor)) as abs_difference_minor_units
from {m}.fct_payout_reconciliation
group by recon_status, processor, currency
order by payouts desc
""",
        },
        {
            "name": "Order-to-settlement matching",
            "display": "table",
            "size": (12, 5),
            "sql": """
select match_status, processor, count(*) as orders
from {m}.fct_order_settlement_matches
group by match_status, processor
order by orders desc
""",
        },
        {
            "name": "Processor B: missing settlement batches",
            "display": "table",
            "size": (12, 5),
            "sql": """
select missing_batch_number, expected_payout_reference
from {m}.fct_processor_b_batch_gaps
order by missing_batch_number
""",
        },
        {
            "name": "FX: realized (5200) and unrealized (5210), USD",
            "display": "bar",
            "size": (12, 6),
            "settings": {
                "graph.dimensions": ["month_end"],
                "graph.metrics": ["realized_fx_loss_usd", "unrealized_fx_loss_usd"],
            },
            "sql": """
select
    month_end,
    sum(if(account_code = '5200', period_net_usd_minor, 0)) / 100 as realized_fx_loss_usd,
    sum(if(account_code = '5210', period_net_usd_minor, 0)) / 100 as unrealized_fx_loss_usd
from {m}.fct_trial_balance
group by month_end
order by month_end
""",
        },
        {
            "name": "Bad debt: negative seller balances and allowance",
            "display": "combo",
            "size": (12, 6),
            "settings": {
                "graph.dimensions": ["month_end"],
                "graph.metrics": ["allowance_usd", "sellers_reserved"],
            },
            "sql": """
select
    month_end,
    sum(allowance_usd_minor) / 100 as allowance_usd,
    countif(is_reserved) as sellers_reserved,
    countif(receivable_usd_minor > 0) as sellers_negative
from {m}.fct_seller_balance_aging
group by month_end
order by month_end
""",
        },
    ],
}

UNIT_ECONOMICS = {
    "name": "Kiln: Unit economics",
    "description": (
        "GMV, take rate and contribution margin by month, region, processor, "
        "and plan. Definitions in METRICS.md. Synthetic data."
    ),
    "cards": [
        {
            "name": "GMV and net revenue, USD",
            "display": "line",
            "size": (12, 6),
            "settings": {
                "graph.dimensions": ["order_month"],
                "graph.metrics": ["gmv_usd", "net_revenue_usd"],
            },
            "sql": """
select order_month, gmv_usd_minor / 100 as gmv_usd, net_revenue_usd_minor / 100 as net_revenue_usd
from {m}.mrt_metrics_monthly
order by order_month
""",
        },
        {
            "name": "Take rate and contribution margin",
            "display": "line",
            "size": (12, 6),
            "settings": {
                "graph.dimensions": ["order_month"],
                "graph.metrics": ["take_rate", "contribution_margin"],
            },
            "sql": """
select order_month, take_rate, contribution_margin
from {m}.mrt_metrics_monthly
order by order_month
""",
        },
        {
            "name": "Contribution margin by buyer region",
            "display": "line",
            "size": (12, 6),
            "settings": {
                "graph.dimensions": ["order_month", "buyer_region"],
                "graph.metrics": ["contribution_margin"],
            },
            "sql": """
select
    order_month,
    buyer_region,
    safe_divide(sum(contribution_usd_minor), sum(net_revenue_usd_minor)) as contribution_margin
from {m}.mrt_unit_economics_monthly
group by order_month, buyer_region
order by order_month
""",
        },
        {
            "name": "Take rate and FX loss by processor",
            "display": "table",
            "size": (12, 6),
            "sql": """
select
    processor,
    sum(gmv_usd_minor) / 100 as gmv_usd,
    safe_divide(sum(net_revenue_usd_minor), sum(gmv_usd_minor)) as take_rate,
    safe_divide(sum(processing_cost_usd_minor), sum(gmv_usd_minor)) as processing_cost_rate,
    safe_divide(sum(realized_fx_loss_usd_minor), sum(gmv_usd_minor)) as fx_loss_rate,
    safe_divide(sum(contribution_usd_minor), sum(net_revenue_usd_minor)) as contribution_margin
from {m}.mrt_unit_economics_monthly
group by processor
""",
        },
        {
            "name": "Refund rate and dispute rate",
            "display": "line",
            "size": (12, 6),
            "settings": {
                "graph.dimensions": ["order_month"],
                "graph.metrics": ["refund_rate", "dispute_rate_count", "chargeback_loss_rate"],
            },
            "sql": """
select order_month, refund_rate, dispute_rate_count, chargeback_loss_rate
from {m}.mrt_metrics_monthly
order by order_month
""",
        },
        {
            "name": "Unit economics by plan",
            "display": "table",
            "size": (12, 6),
            "sql": """
select
    plan_name,
    sum(orders) as orders,
    sum(gmv_usd_minor) / 100 as gmv_usd,
    safe_divide(sum(net_revenue_usd_minor), sum(gmv_usd_minor)) as take_rate,
    safe_divide(sum(contribution_usd_minor), sum(net_revenue_usd_minor)) as contribution_margin,
    safe_divide(sum(refunded_orders), sum(orders)) as refund_rate
from {m}.mrt_unit_economics_monthly
group by plan_name
order by gmv_usd desc
""",
        },
    ],
}

CHURN_AND_RETENTION = {
    "name": "Kiln: Churn and retention",
    "description": (
        "Seller churn (three full months with no orders), account closures, "
        "cohort retention, net revenue retention and LTV to date. Definitions "
        "in METRICS.md. Synthetic data."
    ),
    "cards": [
        {
            "name": "Monthly seller churn rate and revenue churn rate",
            "display": "line",
            "size": (12, 6),
            "settings": {
                "graph.dimensions": ["activity_month"],
                "graph.metrics": ["seller_churn_rate", "revenue_churn_rate"],
            },
            "sql": """
select activity_month, seller_churn_rate, revenue_churn_rate
from {m}.mrt_seller_churn_monthly
where seller_churn_rate is not null
order by activity_month
""",
        },
        {
            "name": "Seller flows: new, returning and churned",
            "display": "bar",
            "size": (12, 6),
            "settings": {
                "graph.dimensions": ["activity_month"],
                "graph.metrics": ["new_sellers", "returning_sellers", "churned_sellers"],
                "stackable.stack_type": "stacked",
            },
            "sql": """
-- Returning = reactivated, plus sellers who joined before the data starts.
-- Churned is negative so it reads as an outflow.
select
    activity_month,
    new_sellers,
    reactivated_sellers + first_seen_sellers as returning_sellers,
    -churned_sellers as churned_sellers
from {m}.mrt_seller_churn_monthly
where seller_churn_rate is not null
order by activity_month
""",
        },
        {
            "name": "Stopped selling vs closed account, sellers per month",
            "display": "line",
            "size": (12, 6),
            "settings": {
                "graph.dimensions": ["activity_month"],
                "graph.metrics": ["churned_sellers", "account_closures", "active_sellers"],
            },
            "sql": """
select activity_month, churned_sellers, account_closures, active_sellers
from {m}.mrt_seller_churn_monthly
where seller_churn_rate is not null
order by activity_month
""",
        },
        {
            "name": "Seller churn rate by plan",
            "display": "line",
            "size": (12, 6),
            "settings": {
                "graph.dimensions": ["churn_month", "plan_name"],
                "graph.metrics": ["seller_churn_rate"],
            },
            "sql": """
-- Plan = the seller's plan in the month before the churn month.
select
    departure_month as churn_month,
    plan_name,
    safe_divide(countif(departure = 'churned'), count(*)) as seller_churn_rate
from {m}.mrt_seller_months
group by churn_month, plan_name
having countif(departure = 'undetermined') = 0
order by churn_month
""",
        },
        {
            "name": "LTV to date per joined seller, by months since joining (USD)",
            "display": "line",
            "size": (24, 6),
            "settings": {
                "graph.dimensions": ["months_since_join"],
                "graph.metrics": ["ltv_contribution_usd", "ltv_net_revenue_usd"],
            },
            "sql": """
-- Pooled over the cohorts that have reached each month: sum of cumulative
-- value / sum of cohort sizes, never an average of cohort averages. Months
-- reached by fewer than three cohorts are left out as too thin to read.
select
    months_since_join,
    sum(cumulative_contribution_usd_minor) / sum(cohort_size) / 100 as ltv_contribution_usd,
    sum(cumulative_net_revenue_usd_minor) / sum(cohort_size) / 100 as ltv_net_revenue_usd,
    count(*) as cohorts
from {m}.mrt_seller_ltv_cohorts
group by months_since_join
having count(*) >= 3
order by months_since_join
""",
        },
        {
            "name": "LTV to date per joined seller by cohort (contribution, USD)",
            "display": "table",
            "size": (24, 8),
            "sql": """
select
    cohort_month,
    any_value(cohort_size) as cohort_size,
    max(if(months_since_join = 1, ltv_per_seller_usd_minor, null)) / 100 as m1,
    max(if(months_since_join = 3, ltv_per_seller_usd_minor, null)) / 100 as m3,
    max(if(months_since_join = 6, ltv_per_seller_usd_minor, null)) / 100 as m6,
    max(if(months_since_join = 12, ltv_per_seller_usd_minor, null)) / 100 as m12,
    max_by(ltv_per_seller_usd_minor, months_since_join) / 100 as to_date
from {m}.mrt_seller_ltv_cohorts
group by cohort_month
order by cohort_month
""",
        },
        {
            "name": "Seller retention by join cohort",
            "display": "table",
            "size": (24, 8),
            "sql": """
select
    cohort_month,
    any_value(cohort_size) as cohort_size,
    max(if(months_since_join = 1, seller_retention, null)) as m1,
    max(if(months_since_join = 2, seller_retention, null)) as m2,
    max(if(months_since_join = 3, seller_retention, null)) as m3,
    max(if(months_since_join = 6, seller_retention, null)) as m6,
    max(if(months_since_join = 12, seller_retention, null)) as m12
from {m}.mrt_seller_cohorts
group by cohort_month
order by cohort_month
""",
        },
        {
            "name": "Net revenue retention by join cohort (vs month 1)",
            "display": "table",
            "size": (24, 8),
            "sql": """
select
    cohort_month,
    max(if(months_since_join = 2, revenue_retention, null)) as m2,
    max(if(months_since_join = 3, revenue_retention, null)) as m3,
    max(if(months_since_join = 6, revenue_retention, null)) as m6,
    max(if(months_since_join = 12, revenue_retention, null)) as m12
from {m}.mrt_seller_cohorts
where month_one_revenue_usd_minor is not null
group by cohort_month
order by cohort_month
""",
        },
    ],
}

DASHBOARDS = [FINANCE_CLOSE, UNIT_ECONOMICS, CHURN_AND_RETENTION]
