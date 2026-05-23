-- Parameter proliferation  (the #1 source of crawl waste)
-- Answers: what share of crawl budget is spent on parameterized URLs
--          (?color=, ?sort=, session IDs, faceted navigation, ...)?
-- Healthy: low single-digit %. High % means facets/filters are generating
--          near-infinite crawlable variants of the same content.
--
-- *** CORRECTNESS PITFALL (do not "simplify" this away) ***
-- DuckDB's regexp_extract returns '' (empty string), NOT NULL, when there is
-- no match. So COUNT(query_string) would count EVERY row and report ~100%.
-- NULLIF(..., '') turns the no-match empty string into a real NULL, so that
-- COUNT() (which ignores NULLs) counts only genuinely parameterized URLs.
--
-- Dialect notes:
--   BigQuery   : REGEXP_EXTRACT returns NULL on no match -> NULLIF unneeded,
--                but harmless to keep.
--   Snowflake  : REGEXP_SUBSTR returns NULL on no match.
--   ClickHouse : extract() returns '' on no match -> the same NULLIF fix applies
--                (use nullIf(extract(uri, '\\?(.*)$'), '')).
WITH crawls AS (
    SELECT NULLIF(regexp_extract(uri, '\?(.*)$', 1), '') AS query_string
    FROM logs
    WHERE verified
)
SELECT
    COUNT(*)                                            AS total_crawls,
    COUNT(query_string)                                 AS parameterized_crawls,
    ROUND(100.0 * COUNT(query_string) / COUNT(*), 2)    AS parameterized_pct
FROM crawls;
