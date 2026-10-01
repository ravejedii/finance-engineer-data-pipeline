-- Every seller is accounted for in each month's churn flows. Returns any month
-- where arrivals do not add up to active sellers, departures do not add up to
-- the sellers active the month before, or the two sides disagree on how many
-- sellers were retained from one month to the next.
select *
from {{ ref('mrt_seller_churn_monthly') }}
where
    active_sellers
    != new_sellers + first_seen_sellers + retained_sellers + reactivated_sellers
    or sellers_at_start
    != retained_from_start + paused_sellers + churned_sellers + undetermined_sellers
    or retained_sellers != retained_from_start
    or seller_churn_rate not between 0 and 1
