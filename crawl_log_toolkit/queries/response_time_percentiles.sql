-- Response-time percentiles served to the crawler
-- Answers: how fast is the origin for the crawler? Google reduces crawl rate
--          when responses get slow, so p95/p99 are the numbers to watch.
-- Healthy: p50 well under ~200ms; p95 under ~1s. Tail latency is what bites.
-- Note: request_time is NULL for formats that don't record it (e.g. plain
--       NCSA Combined); those rows are excluded from the percentiles.
--
-- Dialect notes:
--   BigQuery   : APPROX_QUANTILES(request_time, 100)[OFFSET(95)].
--   Snowflake  : PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY request_time).
--   ClickHouse : quantile(0.95)(request_time).
SELECT
    crawler_category,
    COUNT(*) FILTER (WHERE request_time IS NOT NULL) AS samples,
    ROUND(quantile_cont(request_time, 0.50), 4)      AS p50_seconds,
    ROUND(quantile_cont(request_time, 0.95), 4)      AS p95_seconds,
    ROUND(quantile_cont(request_time, 0.99), 4)      AS p99_seconds,
    ROUND(MAX(request_time), 4)                      AS max_seconds
FROM logs
WHERE verified
GROUP BY crawler_category
ORDER BY samples DESC;
