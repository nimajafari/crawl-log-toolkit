-- Crawl frequency per URL
-- Answers: which URLs does the crawler spend its budget on?
-- Healthy: high-value, indexable URLs at the top; low duplication.
-- Watch for: a single template/path dominating, or near-duplicate URLs.
--
-- Dialect notes:
--   BigQuery   : MAX(timestamp) is fine; use FORMAT_TIMESTAMP for display.
--   Snowflake  : identical.
--   ClickHouse : use any(timestamp) or max(timestamp); LIMIT BY for per-group.
SELECT
    path,
    COUNT(*)               AS crawls,
    COUNT(DISTINCT uri)    AS distinct_uris,   -- same path, different query strings
    MIN(timestamp)         AS first_crawled,
    MAX(timestamp)         AS last_crawled
FROM logs
WHERE verified
GROUP BY path
ORDER BY crawls DESC
LIMIT 50;
