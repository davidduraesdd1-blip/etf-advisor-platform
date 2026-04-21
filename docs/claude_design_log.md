# Claude Design ↔ Claude Code Shared Log

This file is the persistent shared workspace between the two Claude
instances David is collaborating with:

- **Claude design** — the web/desktop planning conversation (strategy,
  research, build-package authoring, risk review).
- **Claude code** — this VS Code extension (implementation, testing,
  commits, file ops).

Both sides read and write this file. David is the transport in between,
but both sides use this as the single source of truth for where things
stand. Replaces ad-hoc copy-paste with a durable on-disk log.

## How to use

### For Claude design (planning side)

Append a new section to the **"Directions from design"** log with:
- Date
- Scope (which sprint / file / decision)
- Numbered directives or numbered questions
- Any risk flags design wants tracked

### For Claude code (this side)

Append a new section to the **"Status from code"** log with:
- Date
- What shipped since last entry (commit hashes, tags, file list)
- Green test count / any reds
- Risk flags code wants raised to planning
- Open questions where code needs direction before proceeding

### For David

Paste design's new directive block into this file under "Directions from
design" before starting a Claude Code session. When Claude Code finishes a
sprint, it appends under "Status from code". Then you paste the status
block back into the planning conversation. One copy each way per cycle.

---

## Directions from design

### 2026-04-21 — Day 2 sprint revised scope

**Source:** planning-side review of the Day-2 9-item plan.
**Disposition:** approved with 4 modifications.

1. **Split portfolio engine port into Phase 1 (blocking) + Phase 2 (best-effort).** Phase 1 = math primitives only, against minimal universe shape {ticker, expected_return, volatility, correlation_with_btc}. Acceptance = 3-ETF (IBIT/FBTC/ETHA) equal-weight case produces non-degenerate Sharpe, VaR, sample_paths.
2. **SEC EDGAR scanner specifics:** add `EDGAR_CONTACT_EMAIL` config constant with placeholder; `daily_scanner()` raises RuntimeError at runtime if placeholder unchanged. User-Agent format `"ETF-Advisor-Platform {email}"`. Document 24-48hr EDGAR index lag in module docstring as known-good.
3. **yfinance circuit breaker:** 3 fails (HTTP 429 OR empty result) in 60s rolling window → flip to Stooq for session. Expose `get_active_price_source()`. In-memory session-scoped.
4. **Two additions:** (a) determinism-lock canary test (seed=42 → bit-exact sample_paths); (b) `docs/port_log.md` tracking every RWA function ported with disposition.

**Risk directions given** (6):
1. RWA-calibrated distribution params: accept as-is Phase 1; log in port_log for Day-3+ retune.
2. ETH correlation: accept Phase-1 simplification + add runtime guard warning when ETH tickers in universe.
3. FRED: use public CSV endpoint (no API key).
4. MC memory: compute 10,000 paths, retain 250 (not 1,000). Add `MONTE_CARLO_PATHS_COMPUTE`, `MONTE_CARLO_PATHS_RETAIN`, `MONTE_CARLO_MEMORY_CEILING_MB` config constants. Cache key must include seed + tier + universe hash + paths-retained.
5. yfinance empty-result ambiguity: count miss only on tickers in seed universe (known history).
6. Determinism: use `np.random.default_rng(42)` explicitly.

**Success bar:** 7 of 9 items clean = successful Day 2. signal_adapter and broker_mock explicitly droppable to Thursday morning without disrupting Day 3.

---

## Status from code

### 2026-04-21 — Day 2 complete

**Commit:** (updated on push — see git log)
**Tag:** `backup-pre-day3-2026-04-22`
**Landed:** 9 of 9 of the revised 11-unit list, including both "droppable" items. Phase 2 of portfolio engine deliberately skipped per success-bar guidance; Day 3 proceeds without it.

**Files added:**
- `core/risk_tiers.py` — tier × category allocation matrix
- `core/portfolio_engine.py` — Phase 1: build_portfolio, compute_portfolio_metrics, run_monte_carlo (w/ paths_retain), cornish_fisher_var. FRED CSV endpoint live. ETH guard active.
- `core/etf_universe.py` — load_universe + daily_scanner with EDGAR runtime guard + 24-48hr lag docstring + token-bucket limiter
- `core/signal_adapter.py` — simple BUY/HOLD/SELL composite (Day 3+ upgrades)
- `integrations/data_feeds.py` — yfinance → Alpha Vantage → Stooq price chain + circuit breaker (session-scoped, known-history-only counting, reset hook)
- `integrations/broker_mock.py` — submit_basket + cancel_basket with Alpaca-shaped return
- `tests/test_portfolio_engine.py` — 23 tests incl. 4 determinism-lock + 2 ETH-guard + 3 Mod-1 acceptance
- `tests/test_etf_universe.py` — 6 tests incl. 2 placeholder-email guard tests
- `tests/test_data_feeds.py` — 9 tests incl. 2 window-expiry + 2 new-ETF-misses-don't-trip tests
- `docs/port_log.md` — every RWA function dispositioned
- `config.py` additions — EDGAR_CONTACT_EMAIL, MONTE_CARLO_*, YF_CIRCUIT_BREAKER_*, max_drawdown_pct per tier

**Test results:** 53 / 53 passing (15 Day-1 smoke + 38 new Day-2). Determinism lock confirmed bit-stable.

**Risks raised for Day 3+:**
- Cornish-Fisher S=-0.4, K=1.0 are RWA-calibrated; retune to crypto-ETF calibration on Day 3 (suggested S=-0.25, K=2.5).
- Student-t(5) CVaR multipliers (1.40, 1.48) are structural placeholders. Retune Day 3+.
- Magdon-Ismail drawdown factor 3.0 is for illiquid RWA; ETFs likely 2.5. Retune Day 3+.
- Phase-1 covariance uses 2-bucket (same-cat = 0.85, cross-cat weighted by correlation_with_btc). Phase 2 (deferred) replaces with full pairwise. ETH-containing universes emit runtime warning until Phase 2 ships.
- FRED CSV endpoint depends on stable URL format; no API key needed. Add key-backed JSON fallback if CSV parsing ever breaks.
- SEC EDGAR search endpoint format (`efts.sec.gov/LATEST/search-index`) is undocumented by SEC; stable for years but could change. Monitor.

**Open questions for design:**
1. Phase 2 portfolio engine — land Day 3 morning or Day 3 afternoon? Day 3 UI can start without it but SEC Marketing Rule backtest display accuracy benefits from the full pairwise correlation model.
2. Is `EDGAR_CONTACT_EMAIL` going to be set in `config.py` (committed) or `.env` (runtime)? Currently placeholder in config.py — safest is to keep config.py placeholder AND read env override at module load. Should I add that env override?
3. Broker_mock slippage range is 5-20 bps — matches retail brokerage baskets. If the real broker integration targets Alpaca Pro (tighter spreads), retune.

---

## Directions from design

*(appended as new sections over time)*

---

## Status from code

*(appended as new sections over time)*
