# us-price-stats

Automated, lossless mirror of the official US price statistics used to measure inflation —
full history, updated by GitHub Actions as new releases land.

| Source | Code | What |
|---|---|---|
| BLS | `cu` | CPI-U — headline CPI, back to 1913 |
| BLS | `cw` | CPI-W — Social Security COLA |
| BLS | `su` | Chained CPI-U — tax brackets |
| BLS | `ap` | Average retail prices |
| BLS | `wp` | PPI, commodity-based |
| BLS | `pc` | PPI, industry-based |
| BEA | `T20804` | PCE price index (Fed's target), optional |

Add/remove surveys in `config.yml`.

## Layout

```
data/bls/<survey>/values/<year>.csv   series_id,year,period,value,footnote_codes
data/bls/<survey>/meta/*.tsv          series, item, area, footnote, period ... lookups
data/bls/<survey>/doc/*.txt           BLS's own file documentation
data/bea/T20804.csv
state/manifest.json                   hashes, latest period, row counts
```

Values are stored as the exact text BLS publishes. `-` = not published
(e.g. most October 2025 indexes, lost to the federal shutdown). `M13` = annual average.

Headline CPI-U (NSA): `grep CUUR0000SA0 data/bls/cu/values/2026.csv`

## Why git and not a database

- **Size fits.** CPI is tens of MB of text; PPI is the bulk. Per-year files keep every file
  far under GitHub's 100 MB limit, and monthly updates only touch the last few years, so git's
  delta compression keeps history growth small. Check `du -sh .git` after the backfill; if PPI
  pushes the repo past ~1 GB, move `pc`/`wp` to their own repo.
- **Revisions for free.** BLS revises seasonally adjusted data every February (5 years back).
  Each sync is a commit + `vintage-*` tag, so `git log -p data/bls/cu/values/2024.csv` shows
  exactly what changed and when — a real-time vintage archive a plain DB wouldn't keep.
- **Free, durable, public, zero ops.** No server, no credentials to rotate, no backups to run.
  Anyone can read it via `raw.githubusercontent.com` or a clone.
- **Still queryable.** DuckDB reads the CSVs directly:
  ```sql
  SELECT * FROM read_csv('data/bls/cu/values/*.csv')
  WHERE series_id = 'CUUR0000SA0' ORDER BY year, period;
  ```

Add a DB only if you later need a public query API or high-frequency joins; it can be
rebuilt from this repo at any time, so git stays the source of truth.

## Setup

```bash
gh repo create us-price-stats --public --source . --push
gh variable set BLS_CONTACT_EMAIL --body "you@example.com"   # BLS 403s without it
gh secret set BEA_API_KEY        # optional, free at apps.bea.gov/API/signup
gh workflow run sync-price-data -f force=true   # initial full backfill
```

Schedule: weekdays 14:07 and 19:07 UTC. A run is cheap when nothing changed — it hashes the
BLS directory listings and only downloads surveys whose files changed.

## Notes

- BLS fronts `download.bls.gov` with Akamai; the User-Agent must contain a contact email
  and **no URL**. If GitHub's runner IPs ever get blocked, switch the fetch to
  `api.bls.gov` v2 (free key, 500 queries/day) or run the workflow on a self-hosted runner.
- Data terms: "BLS.gov cannot vouch for the data or analyses derived from these data after
  the data have been retrieved from BLS.gov."

Test locally: `bash tests/test_sync.sh`
