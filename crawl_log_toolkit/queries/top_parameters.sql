-- Top query-string parameters seen by crawlers
-- Answers: *which* parameters drive proliferation? These are the candidates
--          for robots.txt disallow, rel=canonical, or URL Parameters handling.
-- Healthy: a short list of intentional, content-changing params. Long tails of
--          tracking/session params (utm_*, sessionid, sort, ...) are waste.
--
-- `examples` lists up to 3 distinct URLs carrying each parameter, so the
-- parameter name has concrete context (which pages spawn the long tail).
--
-- Dialect notes:
--   BigQuery   : SPLIT + UNNEST; SPLIT(kv,'=')[OFFSET(0)] for the key;
--                ARRAY_TO_STRING(ARRAY_AGG(DISTINCT uri LIMIT 3), ...) for examples.
--   ClickHouse : arrayJoin(splitByChar('&', ...)); splitByChar('=', kv)[1];
--                arrayStringConcat(arraySlice(groupUniqArray(uri), 1, 3), ...).
WITH params AS (
    SELECT
        uri,
        unnest(
            string_split(NULLIF(regexp_extract(uri, '\?(.*)$', 1), ''), '&')
        ) AS kv
    FROM logs
    WHERE verified
)
SELECT
    split_part(kv, '=', 1)                                  AS parameter,
    COUNT(*)                                                AS occurrences,
    array_to_string((array_agg(DISTINCT uri ORDER BY uri))[1:3], chr(10)) AS examples
FROM params
WHERE kv IS NOT NULL AND kv <> ''
GROUP BY parameter
ORDER BY occurrences DESC, parameter
LIMIT 25;
