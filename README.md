# crawl-log-toolkit

**Turn raw web-server / CDN access logs into verified search-crawler analytics.**

The pipeline is: **parse** many log formats → **verify crawler identity by IP,
not user-agent** → **enrich** → **analyze** with SQL → (optionally) feed
dashboards.

> ### Logs are the only ground truth
> Server / CDN access logs are the only record of what Googlebot (and other
> crawlers) *actually did* on your site. Search Console is aggregated and
> lagged; third-party crawlers only *simulate* a crawl. If you have a
> crawl-budget problem — a big e-commerce catalog, a high-velocity news site, a
> marketplace full of user-generated URLs — your logs already contain the
> answer. This toolkit extracts it.

It is the observability counterpart to edge SEO workers that *capture* verified
bot traffic at the edge; this repo *analyzes* logs from **any** source.

---

## Audiences

- **Platform / backend engineers** wiring this into a log pipeline → start at
  [Install](#install) and [The pipeline](#the-pipeline), then
  [`log-formats/`](#emit-parseable-logs-log-formats) and
  [`dashboards/`](#wire-into-your-stack-dashboards).
- **SEO specialists** running the analyses monthly → skim
  [30-second demo](#30-second-demo) and live in the
  [Metric catalog](#metric-catalog).

---

## Install

Requires **Python 3.11+**. Runtime deps are just `duckdb` and `requests`.

```bash
pip install -e .          # from a clone
# or, once published:     pip install crawl-log-toolkit
```

This installs the `crawl-log` command.

## 30-second demo

A synthetic sample dataset ships with the package, so you can see real output
**with no real logs and no network**:

```bash
# Who is a verified crawler vs a spoofer? (IP-based, not user-agent)
crawl-log verify sample-data/access.log.gz --summary-only --no-network
# -> verify summary: {"total": 440, "accepted": 350, "rejected": 90,
#                     "by_category": {"googlebot": 325, "bingbot": 15, ...}}

# Enrich once, then analyze many times.
crawl-log enrich  sample-data/access.log.gz -o /tmp/enriched.parquet --no-network
crawl-log analyze /tmp/enriched.parquet -q parameter_proliferation
# -> total_crawls 350 | parameterized_crawls 60 | parameterized_pct 17.14

crawl-log analyze /tmp/enriched.parquet -q crawl_trap_detection
# -> /calendar  crawls 200  distinct_urls 200   <-- the planted crawl trap
```

The sample deliberately contains a **spoofed Googlebot** (Googlebot UA from
non-Google IPs — all 90 unverified lines include these), a
**parameter-proliferation** cluster, a **404 spike**, and a **crawl trap**.

## Step-by-step: run it on your own logs

1. **Install** and confirm the command is available:
   ```bash
   pip install -e .          # or: pip install crawl-log-toolkit
   crawl-log --version
   ```

2. **Get a parseable log.** If you already have nginx/Apache/Cloudflare/ALB
   access logs, skip ahead — the format is auto-detected. To emit clean logs,
   copy a `log_format` from [`log-formats/`](log-formats) (the JSON one is
   recommended; it adds `request_time` and `host`). Prefer CDN edge logs over
   origin logs. Logs may be plain or `.gz`.

3. **Sanity-check who is really a crawler** (IP-based). Refresh the published
   ranges first so verification is current:
   ```bash
   crawl-log verify /var/log/nginx/access.log.gz --refresh --summary-only
   # -> verify summary: {"total": ..., "accepted": ..., "rejected": ..., "by_category": {...}}
   ```
   A high `rejected` count is expected — those are spoofers and humans.

4. **Enrich once** into a compressed Parquet file (verifies + extracts `path`):
   ```bash
   crawl-log enrich /var/log/nginx/access.log.gz -o enriched.parquet
   ```
   Got logs split across many files? Enrich each, or glob them in step 5.

5. **Analyze** — run the whole pack, or one metric at a time:
   ```bash
   crawl-log analyze --list                       # see available queries
   crawl-log analyze enriched.parquet --all       # run everything
   crawl-log analyze enriched.parquet -q parameter_proliferation
   crawl-log analyze "logs/*.parquet" -q crawl_trap_detection   # glob multiple files
   crawl-log analyze enriched.parquet --all --format csv -o report.csv
   ```

6. **(Optional) first-crawl latency.** Provide a CSV of `url,published_at`
   (paths matching the normalized `path`) to measure time-to-discovery:
   ```bash
   crawl-log analyze enriched.parquet -q first_crawl_latency --publication-log pubs.csv
   ```

7. **(Optional) wire it into your stack.** Use the reference configs in
   [`dashboards/`](dashboards) to verify and visualize at ingest time
   (Vector / Datadog / Grafana+Loki).

> Running monthly? Steps 3–5 are the routine: `verify --refresh` to keep ranges
> fresh, `enrich` the new period, then `analyze --all` and compare to last month.

## The pipeline

One command per stage; all are gzip-transparent and composable over pipes.

| Stage | Command | In → Out |
|------|---------|----------|
| **parse** | `crawl-log parse access.log.gz` | raw logs → normalized JSONL |
| **verify** | `crawl-log verify access.log.gz --summary-only` | logs → verified-only + summary |
| **enrich** | `crawl-log enrich access.log.gz -o out.parquet` | logs → ZSTD Parquet (`verified`, `crawler_category`) |
| **analyze** | `crawl-log analyze out.parquet --all` | Parquet → metric tables |

```bash
# Pipe parse → enrich:
crawl-log parse access.log.gz | crawl-log enrich --from-jsonl - -o out.parquet

# Classify a single IP:
crawl-log verify --ip 66.249.66.1            # -> googlebot
crawl-log verify --ip 45.83.64.10            # -> not-verified  (exit code 1)

# List / run queries:
crawl-log analyze --list
crawl-log analyze out.parquet --all --format json
crawl-log analyze out.parquet -q first_crawl_latency --publication-log pubs.csv
```

### parse — formats understood

`NCSA Combined` (also nginx default & Apache combined), `nginx JSON`,
`Cloudflare Logpush`, and `AWS ALB`. The format is auto-detected; force it with
`--format {combined,nginx_json,cloudflare,alb}`. Every record is normalized to:

```
timestamp (ISO 8601 UTC) · remote_addr · method · host · uri (with query) ·
path (query stripped) · status · bytes_sent · referer · user_agent · request_time
```

### verify — by IP, never by user-agent

A request claiming to be Googlebot is trusted **only** if its source IP is in
Google's published ranges (or forward-confirmed reverse DNS resolves into
`googlebot.com` / `google.com`). The toolkit ships a vendored snapshot of the
published ranges (Googlebot, Google special crawlers, Google user-triggered
fetchers, Bingbot) so it works offline, and refreshes from the source on a TTL:

```bash
crawl-log verify access.log.gz --refresh        # fetch fresh ranges, then verify
crawl-log verify access.log.gz --no-network      # bundled snapshot only (CI-safe)
crawl-log verify access.log.gz --fcrdns          # reverse-DNS (FCrDNS) instead
```

Unverified traffic is kept as a **separate class** and never mixed into
crawl-budget analysis. A Googlebot UA from a random IP is a spoofer; a Googlebot
IP with a Chrome UA is a real user behind a Google proxy — neither is Googlebot.

### enrich — "enrich once, query many"

Verifies each row by IP, extracts `path`, and writes compressed columnar Parquet
(`COMPRESSION ZSTD`) with two added columns — `crawler_category` and `verified`.
Verification runs in Python and is materialized as columns (so the analytics
need no per-row UDF and no `numpy`); `--anonymize-ips` zeroes the host octet(s)
*after* verification for longer-retention storage.

### analyze — the SQL pack

Each query in [`crawl_log_toolkit/queries/`](crawl_log_toolkit/queries) is
DuckDB SQL written against a single relation named `logs` (a view `analyze`
creates over your Parquet). Each file documents what it answers, what *healthy*
looks like, and the BigQuery / Snowflake / ClickHouse syntax tweaks.

## Metric catalog

| Query | Answers | Healthy looks like |
|-------|---------|--------------------|
| `crawl_frequency_per_url` | Where is crawl budget spent? | High-value, indexable URLs on top |
| `status_distribution_by_day` | Are errors/redirects trending up? | Mostly 200/304; tiny stable 3xx; ~0 5xx |
| `crawl_depth` | How deep does crawling reach? | Important content within ~3–4 segments |
| `response_time_percentiles` | How fast is the origin to crawlers? | p50 ≪200ms, p95 <1s (slow ⇒ reduced crawl rate) |
| `bytes_served` | What does crawling cost in bytes? | Tracks useful content, not junk URLs |
| `first_crawl_latency` | Time from publish → first crawl? | Hours, not days (needs `--publication-log`) |
| `parameter_proliferation` | % of budget on parameterized URLs | Low single digits |
| `top_parameters` | Which params drive proliferation? | A short, intentional list |
| `redirect_404_waste` | Budget burned on 3xx + 404? | < ~5% combined |
| `crawl_trap_detection` | Which prefix has unbounded URL cardinality? | Bounded distinct URLs per prefix |
| `crawl_waste_pct` | One headline waste number | Low; trend it month-over-month |
| `spoofed_crawler_summary` | Who fakes a crawler UA? | Empty-ish; feed the rest to your WAF |

## Emit parseable logs (`log-formats/`)

Reference producer configs whose output `parse` understands out of the box:

- [`log-formats/nginx-seo.conf`](log-formats/nginx-seo.conf) — a Combined format
  **and** a recommended structured-JSON `log_format` (its keys match the parser,
  and it adds `$request_time`/`$host` that Combined omits).
- [`log-formats/apache-seo.conf`](log-formats/apache-seo.conf) — Combined plus an
  SEO variant that prepends the vhost and appends response time in seconds.

Prefer **CDN edge logs** (Cloudflare Logpush / Fastly / CloudFront) over origin
logs: origin logs miss everything served from cache — exactly what crawlers
often receive.

## Wire into your stack (`dashboards/`)

Reference, copy-paste-and-adapt configs:

- [`dashboards/vector.toml`](dashboards/vector.toml) — a Vector transform that
  verifies by subnet with `ip_cidr_contains(cidr, ip) ?? false` and loads ranges
  from the published JSON on a schedule (never hardcoded).
- [`dashboards/datadog.yaml`](dashboards/datadog.yaml) — a Datadog log pipeline
  that drops internal IPs with `exclude_at_match` (RE2 has **no lookahead**).
- [`dashboards/grafana.json`](dashboards/grafana.json) — a Grafana + Loki
  dashboard (verified crawls over time, status mix, spoofed-bot count, top paths).

---

## ⚠️ Caveats — read before trusting the numbers

> **Sampling.** Verify your CDN isn't *sampling* logs before trusting totals. A
> 1%-sampled feed is **unusable** for crawl analysis. (As of 2026, Cloudflare
> Logpush is Business/Enterprise only; some plans sample.)
>
> **IP-range freshness.** Published crawler IP ranges change. The bundled
> snapshot is for out-of-the-box use and tests — in production refresh it
> (`crawl-log verify --refresh`, or schedule a daily fetch) so you neither miss
> real crawlers nor trust stale ranges.
>
> **PII / GDPR.** Access logs contain IP addresses (personal data in many
> jurisdictions). Mind your retention and lawful basis; use
> `enrich --anonymize-ips` to truncate IPs after verification for longer windows.

---

## Development

```bash
pip install -e ".[dev]"
ruff check . && ruff format --check .
pytest                                   # unit + end-to-end, no network
python scripts/generate_sample_data.py   # regenerate the sample + expected fixtures
```

The end-to-end test runs the whole pipeline on the bundled sample and asserts
the analytics equal the ground-truth fixtures in `sample-data/expected/`.

## License & contributing

[MIT](LICENSE). Contributions welcome — see [CONTRIBUTING.md](CONTRIBUTING.md).
