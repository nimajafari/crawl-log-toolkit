-- Crawl depth distribution
-- Answers: how deep into the site hierarchy does the crawler reach?
-- depth = number of path segments ("/a/b/c" -> 3; "/" -> 0).
-- Healthy: important content within ~3-4 clicks gets crawled; a long tail at
--          extreme depth often signals a crawl trap or poor internal linking.
--
-- Dialect notes:
--   BigQuery   : ARRAY_LENGTH(SPLIT(TRIM(path,'/'),'/')).
--   Snowflake  : ARRAY_SIZE(SPLIT(TRIM(path,'/'),'/')).
--   ClickHouse : length(splitByChar('/', trimBoth('/', path))).
SELECT
    CASE
        WHEN path IS NULL OR path = '/' OR path = '' THEN 0
        ELSE len(string_split(trim(path, '/'), '/'))
    END                AS depth,
    COUNT(*)           AS crawls,
    COUNT(DISTINCT path) AS distinct_paths
FROM logs
WHERE verified
GROUP BY depth
ORDER BY depth;
