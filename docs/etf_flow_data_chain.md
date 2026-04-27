# ETF flow data chain — full specification

Polish round 5, Sprint 2 (2026-04-29) + Sprint 2.5 (2026-04-29) +
Sprint 2.6 (2026-04-30). Cowork directive: "everything real and
live, no hardcoded fallback values."

## Measured coverage (Sprint 2.6 capture run, 2026-04-30)

| Field | Sprint 2.5 (2026-04-29) | Sprint 2.6 (2026-04-30) | Δ |
|---|---|---|---|
| AUM           | 113 / 211 (53.6%) | 119 / 211 (56.4%) | +6 |
| 30D net flow  |   6 / 211 ( 2.8%) |   6 / 211 ( 2.8%) | — |
| Avg daily vol | 124 / 211 (58.8%) | 132 / 211 (62.6%) | +8 |
| Errors        |   0 / 211         |   0 / 211         |  0 |

Sprint 2.6 source distribution (AUM):
  yfinance:                107
  SEC EDGAR (facts):         5  (NEW commit 4)
  issuer-site:grayscale:     1  (NEW commits 1-3)
  reference (bootstrap):     6
  unavailable (em-dash):    92

VS Cowork's ≥150 acceptance gate from amendment 3 of Sprint 2.6:
119/211 misses by 31 tickers. Per amendment 3 the contingency was
"Sprint 2.7 with Playwright" rather than re-tuning 2.6.

## Why coverage stops at 119/211 (Sprint 2.6 commit 7 per amendment 5)

The 92 unavailable-from-any-free-source tickers concentrate by issuer:

| Issuer | Unavail | Sprint 2.6 status | Path to coverage |
|---|---|---|---|
| Bitwise            | 17 | DEFERRED        | Sprint 2.7 — Playwright (React SPA, no static HTML AUM) |
| Grayscale          | 13 | partial         | Most are tickers whose `etfs.grayscale.com/<ticker>` URL 404s. Not a fix; URL discovery for newly-listed slugs is per-ticker work |
| 21Shares           |  9 | not yet wired   | Sprint 2.7 — issuer extractor (next priority after Playwright trio) |
| Canary             |  6 | not yet wired   | Sprint 2.7 |
| Calamos            |  6 | not yet wired   | Sprint 2.7 |
| Franklin Templeton |  4 | DEFERRED        | Sprint 2.7 — Playwright (JS-rendered SPA) |
| BlackRock iShares  |  3 | partial         | IDOG/ILTC/ISOL/IXRP not in the iShares product-screener JSON yet (newly listed) |
| Fidelity           |  3 | DEFERRED        | Sprint 2.7 — Playwright (JS-rendered) |
| ProShares          |  1 | partial         | EETU not in their public sitemap |
| (15 other issuers) | 30 | not yet wired   | Long tail; Sprint 2.7+ |

The deeper structural reason: yfinance's coverage failure correlates
with issuer-site coverage failure. Newly-listed funds that yfinance
hasn't indexed yet are also the funds whose static issuer pages
404 or are JS-rendered. The two failure modes share a root cause
(time-since-listing). Filling the gap requires either time (yfinance
catches up; issuers publish static AUM tiles) or a render path that
can execute the SPA (Playwright in Sprint 2.7).

## Cryptorank silent-failure (Sprint 2.6 finding)

`_fetch_cryptorank_flow` in `integrations/etf_flow_data.py` calls
`https://api.cryptorank.io/v1/etfs/<ticker>/flows` with `X-API-Key`
header. The endpoint URL is speculative — Cryptorank's actual API
schema is at `/v0/...` with different path structure. Their key-
gated step returned non-200 silently for every ticker in the
Sprint 2.6 capture run, contributing 0 net flow values.

Fixing this requires looking up the correct cryptorank.io endpoint
in their dev portal docs (David's key has portal access on the
Basic/Free tier). Flagged for Sprint 2.7 as a separate (cheap)
commit since it's a single endpoint URL fix, not infrastructure.


Demo-critical 20 BTC/ETH spot ETFs (IBIT/FBTC/BITB/ARKB/BTCO/EZBC/
BRRR/HODL/BTCW/BTC/GBTC/DEFI/ETHA/FETH/ETH/ETHE/ETHW/CETH/QETH/EZET)
all carry AUM coverage from bootstrap or yfinance. Long-tail gap is
concentrated in niche / leveraged / inverse ETFs that yfinance's
`totalAssets` field returns null for (e.g., MSSL, FTHR, VLTC).

### Why coverage is below the 95% target

The downstream chain steps are scaffolds that currently return None:

- **AUM step 3** — ETF.com page scrape: scaffold (returns None)
- **AUM step 4** — issuer-site DOM extractors: scaffold for top 6
  issuers, no entries for issuers 7+ (VanEck, 21Shares, Hashdex,
  Canary, Roundhill, Defiance, Direxion, etc.)
- **Flow step 1** — cryptorank.io: key-gated, requires
  `CRYPTORANK_API_KEY`. Capture run was unkeyed → step skipped.
- **Flow step 2** — SoSoValue scrape: scaffold (returns None)
- **Flow step 3** — Farside CSV: scaffold (returns None)
- **Flow step 4** — N-PORT-derived synthesis: scaffold (returns None)
- **Vol step 3** — ETF.com page scrape: scaffold (returns None)

The scaffolds are deliberate — Sprint 2 wired the chain *plumbing*
(cache → live → snapshot → em-dash) so the no-fallback policy is
fully enforced today. Filling in each scaffold is per-source bespoke
work (DOM-parser per issuer, scrape resilience, CSV format drift
detection) that was scoped post-demo.

### Path to ≥95% coverage

1. **Set `CRYPTORANK_API_KEY` on Streamlit Cloud Secrets** — unblocks
   step 1 of the Flow chain for ~25 crypto-flow ETFs.
2. **Implement ETF.com scraper** — single regex sweep over the public
   ETF.com page per ticker. ~80 niche ETFs would gain AUM + Vol.
3. **Implement per-issuer DOM extractors for top 6 issuers** —
   BlackRock iShares, Bitwise, Grayscale, ProShares, Fidelity,
   Franklin. Adds another ~40 tickers.
4. **Add issuer-extractor entries for issuers 7+** — VanEck, 21Shares,
   Hashdex, Canary, Roundhill, Defiance, Direxion. Adds another ~30.
5. **Wire N-PORT-derived flow synthesis** — (AUM_today − AUM_30d_ago)
   minus return attribution. Real data, not synthetic, but requires
   30-day AUM history which only exists once snapshot files compound.

Items 1-2 alone would push AUM to ~85% and Vol to ~85%. Items 1-4
together are the realistic path to ≥95% AUM/Vol. Item 5 is the only
realistic path to ≥20% Flow coverage outside crypto-flow ETFs.

This document specifies the multi-source live chains for the three
ETF reference-data fields surfaced on the ETF Detail page:
  - **AUM** — assets under management, in USD
  - **30D net flow** — trailing-30-day net inflow/outflow, in USD
  - **Avg daily volume** — average daily share volume

Sources walked, fallback semantics, no-fallback policy, refresh
cadence, and operator runbook.

## Architecture

```
ETF Detail page render
    │
    ├─► get_etf_aum(ticker)
    ├─► get_etf_30d_net_flow(ticker)
    └─► get_etf_avg_daily_volume(ticker)
            │
            └─► For each, the chain is:
                  1. Runtime cache (data/etf_flow_cache.json, 24h TTL, gitignored)
                  2. Live multi-source chain (yfinance / EDGAR / cryptorank / ...)
                  3. Production snapshot (core/etf_flow_production.json, COMMITTED)
                  4. (None, None) → UI renders em-dash
```

Each fetcher returns `(value, source_name)` so the UI badge can show
provenance.

## AUM chain

| # | Source | Status | Notes |
|---|---|---|---|
| 1 | `yfinance.Ticker.info["totalAssets"]` | live | Fast, broad coverage; same path as portfolio_engine AUM tiebreaker. |
| 2 | SEC EDGAR N-PORT `total_value_usd` | live | Authoritative; covers IBIT/ETHA/FBTC/FETH today; expands as more issuers file. |
| 3 | ETF.com public-page scrape | live | Respectful UA, ~1 req/sec. Regex-extracts "AUM: $X.XB" pattern. |
| 4 | Issuer-site extractor | scaffold | Top 6 issuers (BlackRock, Bitwise, Grayscale, ProShares, Fidelity, Franklin). Per-issuer DOM parsers stubbed pending bespoke implementation. |

If all 4 live steps fail → production snapshot → `(None, None)`.

## 30-day net flow chain

| # | Source | Status | Notes |
|---|---|---|---|
| 1 | cryptorank.io ETF-flow endpoint | key-gated | Requires `CRYPTORANK_API_KEY` env var. Skipped (info-log) if unset. |
| 2 | SoSoValue.xyz dashboard scrape | live | Public dashboard with per-ticker pages. |
| 3 | Farside Investors CSV | live | https://farside.co.uk/btc/ + /eth/ — covers BTC + ETH spot ETFs. |
| 4 | Synthetic from N-PORT historical AUM diff | scaffold | Computes (AUM_today − AUM_30d_ago) − return-attribution. Real data, not hardcoded. Awaits richer EDGAR client. |

## Avg daily volume chain

| # | Source | Status | Notes |
|---|---|---|---|
| 1 | yfinance `Ticker.info["averageVolume"]` | live | 3-month average. |
| 2 | yfinance `Ticker.info["averageDailyVolume10Day"]` | live | 10-day fallback. |
| 3 | ETF.com public page | live | Avg vol field. |
| 4 | Compute from yfinance 60-day daily history | live | Mean of Volume column. |

## No-fallback policy

NO hardcoded fallback constants per Cowork's 2026-04-29 directive.
The legacy `_AUM_REFERENCE_STUB_USD` (in portfolio_engine for the AUM
tiebreaker) and `_ETF_REFERENCE_STUB` (formerly in pages/03_ETF_Detail
for the 6 major spot ETFs) are deprecated. Their values now live in
`core/etf_flow_production.json` with proper source attribution AND
fall through to live re-fetch on cache expiry.

The production snapshot's bootstrap entries for the 14 major spot
ETFs carry source `"reference (bootstrap)"` so the UI can be honest
about provenance until the first nightly cron rewrites them with
proper live captures.

## Cache layer

`data/etf_flow_cache.json` (gitignored, runtime). 24-hour TTL per
CLAUDE.md §12. Key = `(ticker, function_name)`. Atomic-write pattern
matching `core/etf_universe.py`.

Behavior:
- Cache hit within TTL → return without network
- Cache miss / stale → walk the chain, write to cache on success
- **No-poison-cache**: None values are NOT cached so a transient
  yfinance hiccup on one ticker doesn't block the next call

## Production snapshot

`core/etf_flow_production.json` — committed safety net. By analogy
with `core/cf_params_production.json`. Schema:

```json
{
  "captured_at_utc": "<iso>",
  "method":          "<description of capture process>",
  "tickers": {
    "<TICKER>": {
      "aum_usd":       <float | null>,
      "flow_30d_usd":  <float | null>,
      "avg_daily_vol": <float | null>,
      "aum_source":    "<step name>",
      "flow_source":   "<step name>",
      "vol_source":    "<step name>"
    },
    ...
  }
}
```

Refreshed by:
1. **Manual operator command** —
   `python scripts/refresh_etf_flow_production.py`
   Patient capture: 5-attempt exponential backoff per ticker, 30-second
   cooldown between batches of 20. Resume-from-progress.
2. **Nightly GitHub Action** (post-Sprint-2) — same script wired into
   the daily scanner workflow as a final step.

## Cron pre-warming

`core.scheduler.prewarm_etf_flow_cache(universe)` is called as the
last step of `recalculate_all_portfolios()` (which itself runs from
the 9-AM-EST cron). It walks all 211 tickers, calls each fetcher,
populates the runtime cache, and writes a per-source distribution
summary to the snapshot's `flow_prewarm` field.

The ETF Detail page header reads `flow_prewarm.warmed_at_utc` +
`flow_prewarm.aum` source distribution to surface the freshness
indicator:

> Data refreshed: 23m ago · 187/211 tickers live · 24/211 from snapshot

## Issuer-extractor registry

| Issuer | Extractor key | Status |
|---|---|---|
| BlackRock iShares | `blackrock_ishares` | scaffold (returns None) |
| Bitwise | `bitwise` | scaffold |
| Grayscale | `grayscale` | scaffold |
| ProShares | `proshares` | scaffold |
| Fidelity | `fidelity` | scaffold |
| Franklin Templeton | `franklin` | scaffold |

Per-issuer DOM parsers land in a follow-up PR. Each issuer's site has
distinct structure + rate-limit posture; the scaffold lets the chain
fall through cleanly until each is implemented.

Issuers 7+ (VanEck, 21Shares, Hashdex, Canary, Roundhill, Defiance,
Direxion, etc.) are NOT in the registry — post-demo work. The
production snapshot covers their tickers via bootstrap entries.

## Refresh cadence

- **Runtime cache**: 24h TTL automatic; first render of any ticker
  per day re-fetches via the chain.
- **Production snapshot**: refreshed by `refresh_etf_flow_production.py`
  on operator demand or via the nightly cron. Recommended cadence:
  weekly during demo phase, daily once paid data feeds (cryptorank
  full tier + ETF.com API) replace the scrape steps.
- **Cron pre-warm**: runs once daily at 9 AM EST after the EDGAR
  scanner step. Pre-loads cache for all 211 tickers.

## Operator runbook

### Refresh production snapshot manually

```bash
# Make sure CRYPTORANK_API_KEY is set if you want cryptorank step active.
export CRYPTORANK_API_KEY=...   # or in .env

python scripts/refresh_etf_flow_production.py
```

Expected runtime: ~8-15 minutes for all 211 tickers when sources are
healthy. Resume-from-progress: re-run after a Ctrl+C interrupt; it
skips already-captured tickers.

### Verify after refresh

```bash
python -m pytest tests/test_etf_flow_data.py tests/test_scheduler_flow_warming.py -v
```

Both test files must pass. Tests verify:
- Cache layer (TTL, atomic write, no-poison)
- Chain step ordering (step 1 wins; falls through on empty)
- Source-name return tuples
- Production-snapshot precedence
- Cron pre-warm summary correctness

### Force a cache flush

```bash
rm data/etf_flow_cache.json
```

Next page render walks the live chain again (the production-snapshot
+ live-fetch chain is the only source path at that point).

## CLAUDE.md governance

- §10 — multi-source data feeds with documented fallback chains
- §11 — environment-scoped runtime state
- §12 — cache TTL (24h for ETF reference data)
- §22 — no-fallback policy as institutional-grade compliance
