-- Every query must return zero rows before creating a 1:* relationship.
-- The first query must return exactly 65 total and 65 distinct dealer keys.
SELECT COUNT(*) AS total_dealers,COUNT(DISTINCT dealer_key) AS distinct_dealer_keys
FROM gold_dim_dealer_v1;
SELECT date_key,COUNT(*) FROM gold_dim_date_v1 GROUP BY date_key HAVING COUNT(*)>1;
SELECT employee_id,COUNT(*) FROM gold_dim_employee_v1 GROUP BY employee_id HAVING COUNT(*)>1;
SELECT dealer_key,COUNT(*) FROM gold_dim_dealer_v1 GROUP BY dealer_key HAVING COUNT(*)>1;
SELECT location_key,COUNT(*) FROM gold_dim_location_v1 GROUP BY location_key HAVING COUNT(*)>1;

-- Referential-integrity checks. Rows returned here are orphan fact keys.
SELECT DISTINCT f.employee_id FROM gold_fact_plan_v1 f LEFT JOIN gold_dim_employee_v1 d USING(employee_id) WHERE f.employee_id<>'' AND d.employee_id IS NULL;
SELECT DISTINCT f.dealer_key FROM gold_fact_plan_v1 f LEFT JOIN gold_dim_dealer_v1 d USING(dealer_key) WHERE f.dealer_key<>'' AND d.dealer_key IS NULL;
SELECT DISTINCT f.location_key FROM gold_fact_plan_v1 f LEFT JOIN gold_dim_location_v1 d USING(location_key) WHERE f.location_key<>'' AND d.location_key IS NULL;
