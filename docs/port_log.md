# Port Log — rwa-infinity-model/portfolio.py → etf-advisor-platform

Tracks every function touched during the Day-2 Phase-1 port. Format:

    [function_name] — [copied verbatim | adapted (see diff) | dropped (reason)]

Updated live as the port progresses. Purpose: when the partner codebase
arrives mid-week and asks "where did X go," the answer is here — not in
git archaeology.

## Phase 1 — Day 2 (Wednesday 2026-04-22)

**get_live_risk_free_rate** — adapted. Replaces the `import data_feeds` call
with a direct HTTP GET to the FRED public CSV endpoint
(`fred.stlouisfed.org/graph/fredgraph.csv?id=DGS3MO`, no API key required
per planning-side Risk 3 direction). 2-hour in-memory cache preserved.
4.25% fallback preserved verbatim. _rfr_cache module-level dict preserved.

**_mc_cache / _mc_cache_key** — adapted. Cache-key input shape changed from
{tier, yield, vol, value, n, ids} to {tier_name, value, n, tickers, seed,
paths_retain} per planning-side Risk 4 direction (cache key must include
seed, tier, universe hash, AND paths-retained count).

**build_portfolio** — adapted.
  - Signature changed: `(tier: int, portfolio_value_usd, assets)` →
    `(tier_name: str, universe, portfolio_value_usd)` to match planning-side
    Mod-1 directive.
  - Dropped `rank_assets_for_tier` dependency (used RWA scoring).
    Replaced with straight issuer-diversity + expense-ratio sort within
    category.
  - Dropped `get_all_rwa_latest` DB read path entirely; universe is now a
    required argument.
  - Dropped `CATEGORY_COLORS` dependency.
  - Kept: 30% max-single-position cap, 3 ETFs max per category, weight
    normalization logic (last holding absorbs rounding remainder).

**compute_portfolio_metrics** — adapted.
  - Dropped `_cap_yield` logic (PT/YT Pendle-token specific, irrelevant
    to ETFs).
  - Dropped `_risk_to_vol` entirely (RWA risk-score → vol mapping).
    Replaced with direct per-ETF `volatility` field from the universe.
  - Dropped `CATEGORY_CORRELATIONS` lookup (17-category matrix, RWA-specific).
    Phase 1 uses a simplified 2-bucket model: same-category corr=0.85,
    cross-category corr = correlation_with_btc weighted pairwise.
    Phase 2 will introduce full pairwise correlation per ETF.
  - Dropped `CHAIN_VOL_PREMIUM` entirely (ETFs are not chain-scoped).
  - Kept verbatim: Magdon-Ismail drawdown approximation (σ×√T×f with
    f=3.0). Will retune to f=2.5 in Phase 2 — ETFs have less illiquidity
    drag than RWA, but 3.0 is acceptable for Phase 1 structural correctness.
  - Kept verbatim: Sortino ratio formula (Sortino & van der Meer 1991),
    Calmar ratio, diversification ratio.
  - Kept: Student-t(5) CVaR multipliers (1.40 for 95%, 1.48 for 99%)
    as structural placeholders. **PHASE-1 FLAG:** these multipliers
    were calibrated for illiquid RWA tail risk. Crypto ETFs have higher
    daily-return kurtosis than equities but lower than tokenized RWAs —
    multipliers likely overstate ETF tail risk slightly. Retune on Day 3+.

**_gaussian_var / cornish_fisher_var** — copied verbatim. Rename only
(now exposed as public `cornish_fisher_var`). Cornish-Fisher expansion
with S=-0.4, K=1.0 preserved. **PHASE-1 FLAG:** S and K parameters were
calibrated for illiquid RWA distributions. Retain-as-is per planning-side
Risk 1 direction. Day-3+ retune: for crypto ETFs a reasonable first-pass
is S=-0.25, K=2.5 (heavier-tailed than equity, less skew than illiquid
RWA). Do not retune in Phase 1.

**_empty_metrics** — copied verbatim.

**run_monte_carlo** — adapted.
  - Dropped: audit_score jump-intensity adjustment (pulled audit data
    from RWA_UNIVERSE which we don't have).
  - Dropped: `_cap_yield` in daily_return derivation.
  - Changed per planning-side Risk 4: compute uses `MONTE_CARLO_PATHS_COMPUTE`
    (10,000) but only `MONTE_CARLO_PATHS_RETAIN` (250) paths are returned
    in `sample_paths`. Previously returned 50 paths; the 250 cap is both
    higher resolution AND explicit in config.
  - Kept verbatim: Merton jump-diffusion (intensity 0.5/year base),
    Student-t ν=4 diffusion with _T_DOF / _t_std standardization,
    Poisson arrivals × Normal jump size, percentile computation,
    histogram bucketing.
  - Random source: `np.random.default_rng(42)` explicit (was already
    the case in RWA source). Confirmed bit-stable across numpy ≥ 1.17.

## Dropped entirely (not ported)

**CATEGORY_CORRELATIONS** (17×17 matrix) — RWA asset classes only.
  Replaced in Phase 1 with simple same/cross-category buckets.
  Full pairwise correlation arrives in Phase 2.

**CHAIN_VOL_PREMIUM** — RWA blockchain maturity adjustment. Irrelevant
  for ETFs (wrapped security, not chain-scoped).

**score_asset / score_assets_batch / rank_assets_for_tier** — RWA scoring
  engine using yield, risk, liquidity, regulatory, audit scores.
  Replaced with straight expense-ratio sort within category.

**compute_efficient_frontier** — not required for Day 2; Day 3+ consideration.

**stress_test_correlations** — RWA-specific crisis scenarios. Not required
  for Day 2.

**calculate_portfolio_duration / DV01** — fixed-income RWA specific.
  May resurface if we add tokenized-treasury ETFs to the universe.

**calculate_portfolio_liquidity / redemption_speed** — RWA-specific
  (on-chain redemption windows). ETFs have T+1 settlement universally;
  not needed.

**compute_factor_tilted_portfolio / optimize_factor_portfolio** — not
  required for Day 2.

**kelly_rebalance_size** — position-sizing primitive; may port in Phase 2
  or Day 3 for the "Execute basket" flow.

**check_rebalance_needed** — portfolio drift detection. Simple enough to
  reimplement when needed; not ported.

**build_all_portfolios / portfolio_comparison_df** — convenience wrappers.
  Not required for Day 2 core math.
