-- requires: publications
-- First-crawl latency for new URLs
-- Answers: how long after you publish a URL does a verified crawler first fetch
--          it? This is the real "time to discovery" — Search Console can't tell
--          you this per-URL or this freshly.
-- Healthy: hours, not days, for important URLs. Long latency (or NULL =
--          never crawled) points at weak internal linking or sitemap issues.
--
-- Requires a `publications` relation with columns (url, published_at). Supply
-- it via:  crawl-log analyze ... --publication-log publication_log.csv
-- where the CSV's `url` matches the normalized `path`.
--
-- Dialect notes:
--   BigQuery   : TIMESTAMP_DIFF(first_crawled_at, published_at, HOUR).
--   Snowflake  : DATEDIFF('hour', published_at, first_crawled_at).
--   ClickHouse : dateDiff('hour', published_at, first_crawled_at).
SELECT
    p.url,
    p.published_at,
    MIN(l.timestamp)                                          AS first_crawled_at,
    date_diff('hour', p.published_at, MIN(l.timestamp))       AS latency_hours
FROM publications p
LEFT JOIN logs l
       ON l.path = p.url
      AND l.verified
GROUP BY p.url, p.published_at
ORDER BY latency_hours DESC NULLS FIRST;
