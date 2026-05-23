-- Spoofed-crawler summary
-- Answers: who is lying about being a crawler? These are requests whose
--          user-agent claims to be Googlebot/Bingbot/Applebot but whose source
--          IP is NOT in the published ranges (so `verified` is false).
-- This is the whole point of IP verification: never let these into crawl-budget
-- analysis. Use this list for WAF / rate-limit rules.
--
-- Dialect notes:
--   BigQuery   : use REGEXP_CONTAINS(LOWER(user_agent), r'googlebot|bingbot|applebot').
--   ClickHouse : positionCaseInsensitive(user_agent, 'googlebot') > 0, etc.
SELECT
    remote_addr,
    COUNT(*)              AS requests,
    any_value(user_agent) AS example_user_agent
FROM logs
WHERE NOT verified
  AND (
        user_agent ILIKE '%googlebot%'
     OR user_agent ILIKE '%bingbot%'
     OR user_agent ILIKE '%applebot%'
  )
GROUP BY remote_addr
ORDER BY requests DESC
LIMIT 25;
