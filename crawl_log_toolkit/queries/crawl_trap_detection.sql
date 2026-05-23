-- Crawl-trap detection
-- Answers: which path prefix is generating a near-unbounded number of distinct
--          URLs (infinite calendars, unbounded search/filter pages, faceted
--          navigation, session-in-path)? These silently swallow crawl budget.
-- Method: group by the first path segment; a prefix with very high
--          COUNT(DISTINCT path) is the signature of a trap.
-- Healthy: distinct_urls per prefix stays bounded and roughly tracks real
--          content count. Tune the HAVING threshold to your site's scale.
--
-- Dialect notes:
--   BigQuery   : SPLIT(path,'/')[SAFE_OFFSET(1)] for the first segment.
--   Snowflake  : SPLIT_PART(path,'/',2).
--   ClickHouse : splitByChar('/', path)[2].
WITH crawls AS (
    SELECT path, '/' || split_part(path, '/', 2) AS prefix
    FROM logs
    WHERE verified
)
SELECT
    prefix,
    COUNT(*)                                       AS crawls,
    COUNT(DISTINCT path)                           AS distinct_urls,
    ROUND(COUNT(DISTINCT path) * 1.0 / COUNT(*), 3) AS distinct_ratio
FROM crawls
GROUP BY prefix
HAVING COUNT(DISTINCT path) >= 50      -- tune to your catalog size
ORDER BY distinct_urls DESC;
