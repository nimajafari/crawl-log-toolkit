-- Status-code distribution by day
-- Answers: is the crawler increasingly hitting errors/redirects over time?
-- Healthy: overwhelmingly 200/304; small, stable 3xx; near-zero 5xx.
-- Watch for: a 404 spike (dead links/removed pages) or 5xx (origin trouble),
--            either of which depresses crawl rate.
--
-- Dialect notes:
--   BigQuery   : DATE(timestamp).
--   Snowflake  : TO_DATE(timestamp) or timestamp::date.
--   ClickHouse : toDate(timestamp).
SELECT
    CAST(timestamp AS DATE) AS day,
    status,
    COUNT(*)                AS crawls
FROM logs
WHERE verified
GROUP BY day, status
ORDER BY day, status;
