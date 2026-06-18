-- Redirect & error URLs  (the offending paths)
-- Answers: which exact URLs return 3xx redirects or 4xx errors to verified
--          crawlers? `redirect_404_waste` tells you HOW MUCH budget this burns;
--          this tells you WHERE -- the paths to actually fix.
-- Healthy: a short list. Collapse redirect chains, fix or return 410 for dead
--          URLs, and repair the internal links/sitemap entries that keep
--          pointing crawlers at them.
-- Note:    304 Not Modified is excluded on purpose -- it is healthy conditional
--          -GET caching, not waste.
--
-- Dialect notes:
--   BigQuery   : MAX(timestamp) AS last_crawled; standard GROUP BY.
--   Snowflake  : identical.
--   ClickHouse : argMax(timestamp, ...) if you also want a representative row.
SELECT
    path,
    status,
    COUNT(*)        AS crawls,
    MAX(timestamp)  AS last_crawled
FROM logs
WHERE verified
  AND status <> 304
  AND (status BETWEEN 300 AND 399 OR status BETWEEN 400 AND 499)
GROUP BY path, status
ORDER BY crawls DESC, status, path;
