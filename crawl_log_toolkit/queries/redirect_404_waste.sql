-- Redirect + 404 waste
-- Answers: how much crawl budget is burned on redirects and dead URLs?
-- Healthy: < ~5% combined. Every 3xx is a wasted hop; every 404 is budget
--          spent learning nothing. Both are usually fixable (update links,
--          collapse redirect chains, return 410 for truly gone content).
--
-- Dialect notes:
--   BigQuery / Snowflake : COUNTIF / COUNT(CASE WHEN ...).
--   ClickHouse           : countIf(status BETWEEN 300 AND 399).
SELECT
    COUNT(*)                                                       AS total_crawls,
    COUNT(*) FILTER (WHERE status BETWEEN 300 AND 399)             AS redirects,
    COUNT(*) FILTER (WHERE status = 404)                           AS not_found,
    COUNT(*) FILTER (WHERE status BETWEEN 500 AND 599)             AS server_errors,
    ROUND(100.0 * COUNT(*) FILTER (
        WHERE status BETWEEN 300 AND 399 OR status = 404
    ) / COUNT(*), 2)                                               AS waste_pct
FROM logs
WHERE verified;
