-- Crawl waste %  (headline metric)
-- Answers: one number for "what fraction of crawl budget is being wasted?"
-- waste = crawls on parameterized URLs OR any non-2xx/3xx-good response, i.e.
--         budget that did not advance fresh, indexable content.
-- Healthy: low. This is the single number to trend month over month.
WITH c AS (
    SELECT
        (NULLIF(regexp_extract(uri, '\?(.*)$', 1), '') IS NOT NULL) AS parameterized,
        (status >= 300)                                            AS not_ok
    FROM logs
    WHERE verified
)
SELECT
    COUNT(*)                                                          AS total_crawls,
    COUNT(*) FILTER (WHERE parameterized)                             AS parameterized_crawls,
    COUNT(*) FILTER (WHERE not_ok)                                    AS non_2xx_crawls,
    COUNT(*) FILTER (WHERE parameterized OR not_ok)                   AS wasted_crawls,
    ROUND(100.0 * COUNT(*) FILTER (WHERE parameterized OR not_ok) / COUNT(*), 2)
                                                                      AS crawl_waste_pct
FROM c;
