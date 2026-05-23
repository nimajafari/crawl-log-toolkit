-- Top query-string parameters seen by crawlers
-- Answers: *which* parameters drive proliferation? These are the candidates
--          for robots.txt disallow, rel=canonical, or URL Parameters handling.
-- Healthy: a short list of intentional, content-changing params. Long tails of
--          tracking/session params (utm_*, sessionid, sort, ...) are waste.
--
-- Dialect notes:
--   BigQuery   : SPLIT + UNNEST; SPLIT(kv,'=')[OFFSET(0)] for the key.
--   ClickHouse : arrayJoin(splitByChar('&', ...)); splitByChar('=', kv)[1].
WITH params AS (
    SELECT unnest(
        string_split(NULLIF(regexp_extract(uri, '\?(.*)$', 1), ''), '&')
    ) AS kv
    FROM logs
    WHERE verified
)
SELECT
    split_part(kv, '=', 1) AS parameter,
    COUNT(*)               AS occurrences
FROM params
WHERE kv IS NOT NULL AND kv <> ''
GROUP BY parameter
ORDER BY occurrences DESC, parameter
LIMIT 25;
