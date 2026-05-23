-- Bytes served to verified crawlers
-- Answers: how much bandwidth does crawling cost, and where does it go?
-- Healthy: bytes track useful content. Large totals on low-value or
--          parameterized paths is wasted transfer and wasted crawl budget.
--
-- Dialect notes:
--   BigQuery / Snowflake / ClickHouse : identical aggregate functions.
SELECT
    crawler_category,
    COUNT(*)                       AS crawls,
    SUM(bytes_sent)                AS total_bytes,
    ROUND(AVG(bytes_sent), 0)      AS avg_bytes,
    ROUND(SUM(bytes_sent) / 1048576.0, 2) AS total_mib
FROM logs
WHERE verified
GROUP BY crawler_category
ORDER BY total_bytes DESC;
