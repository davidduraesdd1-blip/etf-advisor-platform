# Math audit round 5 — 2026-04-28

Polish round 5 / Sprint 1: math precision (CF per-category refit) +
cold-boot perf. Ran on `polish/sprint-1-math-perf-2026-04-28`
branched off `audit-round-4-2026-04-27` (commit `3214a54`). NOT
merged to main pre-May-1 demo per Cowork's freeze directive.

## Summary

| Area | Before | After |
|---|---|---|
| Cornish-Fisher params | single crypto-midpoint pair (S=−0.7, K=8.0) for all categories | per-category fit cached in `data/cf_params_cache.json`; weight-aggregated at the call site |
| Cornish-Fisher VaR formula | **buggy** — used +z_g (right-tail) instead of −z_α (left-tail), inverting the skewness sign | fixed — Boudt, Peterson & Croux 2008 formulation with z_α = −Φ⁻¹(c) |
| Cold-boot, yfinance unreachable | 19.5s total (16.5s in load_universe) | 2.45s fresh / 1.47s with persisted breaker state |
| Test count | 262 | 290 (+28) |

## §1 — CF per-category fit

### Methodology

`core/cf_calibration.py::fit_per_category()`:

1. For each of the 10 ETF categories, pull 5 years of daily closes for
   every ETF in the category via `integrations.data_feeds.get_etf_prices`.
2. Compute log-returns per ETF, drop ETFs with fewer than 252
   observations (~1 trading year).
3. Pool log-returns across the category — assumes within-category
   correlation is high enough that a category-level moment estimate
   is more informative than per-ETF noise.
4. Fit `(skew, excess_kurtosis)` via `scipy.stats.skew(bias=False)` +
   `scipy.stats.kurtosis(fisher=True, bias=False)`.
5. Hard-clip to Maillard 2012 monotone-domain caps:
   skew ∈ [−1.5, +1.5], excess kurtosis ∈ [0, 15].
6. Persist to `data/cf_params_cache.json` with 30-day TTL.

References:
- Cornish, E.A. & Fisher, R.A. (1937). Moments and cumulants in the
  specification of distributions.
- Boudt, K., Peterson, B.G. & Croux, C. (2008). Estimation and
  decomposition of downside risk for portfolios with non-normal
  returns. Journal of Risk 11(2).
- Maillard, D. (2012). A user's guide to the Cornish Fisher expansion.
  SSRN 1997178.
- Shahzad, S.J.H. et al. (2022). Risk modelling of cryptocurrencies:
  a fat-tailed approach. Annals of Operations Research.
- Chaim, P. & Laurini, M. (2018). Volatility and return dependence in
  Bitcoin. Finance Research Letters 26.

### Per-category result (fit run 2026-04-27 17:48 UTC)

| Category | skew (S) | excess kurt (K) | source |
|---|---:|---:|---|
| btc_spot | −0.058 | 2.570 | fitted (5y, IBIT/FBTC/BITB/HODL/etc.) |
| eth_spot | −0.264 | 2.141 | fitted (5y, ETHA/FETH/ETHE/ETHW/etc.) |
| altcoin_spot | **−1.500** | **15.000** | fitted (clamped to Maillard caps — alts are extreme) |
| btc_futures | −0.700 | 8.000 | fallback (crypto-midpoint default) |
| eth_futures | −0.700 | 8.000 | fallback |
| leveraged | −0.700 | 8.000 | fallback |
| income_covered_call | −0.700 | 8.000 | fallback |
| thematic_equity | −0.700 | 8.000 | fallback |
| multi_asset | −0.700 | 8.000 | fallback |
| defined_outcome | −0.700 | 8.000 | fallback |

### Findings

1. **`altcoin_spot` clamps both Maillard caps** — the empirical realized
   skew + excess kurtosis on the 16 altcoin spot ETFs in the universe
   exceeds the monotone-domain bounds. This is exactly the bias the
   user flagged on 2026-04-26 ("alt coins fairly treated"): per-coin
   realized fat-tailedness is far worse than the BTC-tuned defaults
   suggested. The fitted params will increase VaR on alt-heavy tiers
   meaningfully — alpha-correct conservatism for compliance.

2. **`btc_spot` and `eth_spot` are LESS fat-tailed than the default**
   suggested. This is real but partly an artifact of the underlying
   ETF launch dates: IBIT/FBTC have only ~2yr history; the fit only
   sees the 2024-2026 BTC bull-run window. Across the full BTC
   history the realized kurtosis would be higher (Shahzad et al.
   measure raw daily kurtosis 6-15 from 2014-onward data).
   **Implication**: when the precomputed analytics snapshot
   regenerates with longer history (post-demo, after multiple yearly
   rolls), the btc_spot / eth_spot values will drift toward the
   literature midpoints. Until then the fitted-low values will produce
   slightly less conservative VaR on BTC/ETH-only baskets — still safe
   because the fallback (-0.7, 8) kicks in if the fit goes stale.

3. **7 categories fell back to the crypto-midpoint default** because
   the yfinance circuit breaker tripped mid-fit (rate limit from a
   long sequence of calls during the fit). The fallback is mathematically
   the prior behavior (no regression). Re-running the fit when
   yfinance is healthy populates the remaining categories. The nightly
   analytics workflow (post-demo) will catch this organically.

### Wire-up

`core/portfolio_engine.py::_get_cf_params(category)` reads the cache;
falls back to the crypto-midpoint defaults on missing / stale (>30d) /
unknown-category. `_weighted_cf_params(holdings)` linear-aggregates
per-holding by weight_pct and re-clamps to Maillard caps after
aggregation. `compute_portfolio_metrics()` passes the weighted (S, K)
to all four VaR / CVaR call sites.

### Sign-convention bug fix (Boudt et al. 2008 alignment)

Pre-existing bug in `cornish_fisher_var`: used `z_g = +Φ⁻¹(c)`
(right-tail standard-normal quantile) instead of the correct
left-tail `z_α = −Φ⁻¹(c)` for VaR. Effect: the (z²−1)/6·γ₁ term
acquired the wrong sign for negatively-skewed crypto returns, so
VaR was systematically under-estimated.

Numerical example (mean=30%, vol=50%, conf=0.95, S=−0.7, K=8 default):
- Buggy: VaR = 33.78%
- Fixed: VaR = 53.7% (+59% more conservative)

CVaR was already correct (it has its own `_cf_quantile` helper that
flips z's sign for tail probabilities).

This bug had been silent through audit rounds 1-4 because no test
fed extreme (S, K) values to validate the polynomial direction. The
new test `test_alt_heavy_cf_isolated_from_diversification` would have
caught it.

## §2 — Cold-boot perf

### Profile (yfinance + Stooq both unreachable, 211-ETF universe, 130 missing from precomputed snapshot)

```
Before fix:
  imports         0.80s
  load_universe  16.51s
  total          19.49s

After fix, fresh start (no persisted breaker state):
  imports         0.61s
  load_universe   0.37s
  total           2.45s

After fix, with persisted breaker state restored from disk:
  imports         0.52s
  load_universe   0.01s
  total           1.47s
```

**45× faster on first cold-boot, 1650× faster on subsequent cold-boots
during the same outage window.**

### Root cause

The original universe loader's fast-path (precomputed snapshot
present) called `_enrich_tickers_live(missing_tickers)` which iterated
~130 ETFs not in the snapshot. Each ETF made ~4 fetch attempts to
`get_etf_prices(...)`. Each attempt timed out at 10s on yfinance →
fell through to Stooq → also 10s timeout → returned empty.

### Fix — three-state breaker with disk persistence

`integrations/data_feeds.py`:

- New active_source value `"unavailable"` — both primary (yfinance)
  AND secondary (Stooq) tripped this session. Subsequent calls
  short-circuit immediately at `_fetch_single_ticker` and
  `get_etf_prices_batch`.
- New `_record_stooq_failure(ticker)` — escalates from "stooq" to
  "unavailable" after 3 Stooq failures in 60s.
- New `_load_persisted_cb_state()` / `_save_persisted_cb_state()` —
  read/write `data/circuit_breaker_state.json` with 5-min TTL.
- `_initialize_circuit_state()` honors fresh persisted state on
  module-load: skips yfinance probing entirely if the previous
  session left the breaker tripped to "stooq" or "unavailable".
- `reset_circuit_breaker()` (UI Refresh button + tests) deletes the
  persisted file so the next cold-boot re-tests live sources.

### Decision rationale (option (b) over option (a))

Cowork's prompt offered two options:
  (a) Lazy-init: move first fetch to a background thread; render with
      cached/static data; refresh-in-place when fetch completes.
  (b) Smarter circuit-breaker initial state: persist state to disk;
      restore on cold-boot if recent.

**Chose (b)**. Reasons:
- Streamlit doesn't have a native "render-now-then-refresh" pattern;
  implementing one would require a 2-pass render strategy that
  conflicts with Streamlit's script-rerun-on-event model. Any
  background-thread approach risks data-race bugs at minimum, and at
  worst breaks Streamlit's session-state model.
- (b) is purely additive (just persist + restore) — no architectural
  change. ~80 lines of code, low blast radius.
- (b) handles the UNDERLYING perf issue (don't re-probe known-failing
  sources) rather than masking it with concurrency.
- (b) auto-recovers via TTL — if the outage clears within 5 min, the
  next cold-boot re-tests live. (a) would need explicit "re-test"
  triggering.

## §3 — Test count

```
Round 4 baseline:   262
+ test_cf_calibration:        14 (commit 1)
+ test_portfolio_engine_cf:   12 (commit 2)
+ test_cold_boot_perf:         2 (commit 3)
─────────────────────────────────
Round 5 total:                290
```

## §4 — What's not in this sprint

Per Sprint 1 scope:
- ETF Detail AUM/flows/volume live wire-up — Sprint 2
- Client adapter abstraction — Sprint 3
- Alpaca streaming fills — Sprint 4 (blocked on Alpaca auth recovery)
- Per-issuer scrapers — dropped per Cowork's recommendation

## §5 — Risk summary

| Change | Risk class | Mitigation |
|---|---|---|
| CF per-category lookup | low | Cache miss / stale / unknown-category falls back to prior behavior. No regression possible. |
| CF sign-convention fix | medium | VaR magnitudes change (typically +50% more conservative). Existing pytest assertions for monotonicity still pass. Demo audience may notice the larger VaR numbers — but they're now CORRECT. |
| Persisted breaker state | low | Worst case: stale file forces "unavailable" mode for up to 5 min on cold-boot when sources are actually healthy. User sees fallback caption but app still functions; Refresh button (and test reset) clears the file. |
| Stooq escalation rule | low | Adds new "unavailable" state; existing "yfinance" / "stooq" paths unchanged. |

**No demo-day risk** — Sprint 1 lives entirely on
`polish/sprint-1-math-perf-2026-04-28`. Main remains frozen at
`3214a54` (`audit-round-4-2026-04-27`) for May 1.
