# Audit round 3 — 2026-04-27

Re-audit after the universe expansion (82 → 211 ETFs) + auto-approval
scanner + auto-rebalance hook landed earlier today. Identical scope
to round 2 (`docs/math_audit_round_2_2026-04-26.md`) plus three new
audits covering the 2026-04-27 additions.

## Results

| # | Audit | Verdict |
|---|---|---|
| 1 | Universe integrity (schema, duplicates, categories) | **PASS** |
| 2 | `build_portfolio` math across all 5 tiers | **PASS** |
| 3 | Per-altcoin underlying coverage | **PASS** |
| 4 | Auto-classify decision boundary | **PASS** |
| 5 | `recalculate_all_portfolios` end-to-end | **PASS** |
| 6 | Daily-scanner cron + workflow wiring | **PASS** |
| 7 | Cornish-Fisher S/K constants | **PASS** |
| 8 | Pytest suite | **262/262 passing** |
| 9 | Prod verifier | **9/9 passing** |

## Detail

### 1. Universe integrity
- 211 ETFs, 0 duplicate tickers, all 211 carry the required schema
  fields (`ticker`, `issuer`, `category`, `name`, `expense_ratio_bps`,
  `underlying`).
- 10/10 known categories represented; no unknown category strings.

### 2. build_portfolio across 5 tiers (per CLAUDE.md §9 / §13)
Every tier produces:
- weights summing to 100.00% (within rounding tolerance)
- max single-position weight ≤ MAX_SINGLE_POSITION_PCT (30%)
- max-drawdown estimate ≤ tier ceiling
- finite Sharpe ratio in (-10, +50)
- non-negative VaR

| Tier | n | max_w | Sharpe | MDD | VaR95 |
|---|---|---|---|---|---|
| Ultra Conservative | 9  | 20.00% | +0.46 | 15.0% | 29.4% |
| Conservative       | 12 | 20.00% | +0.46 | 25.0% | 34.9% |
| Moderate           | 9  | 18.33% | +0.46 | 40.0% | 39.2% |
| Aggressive         | 12 | 15.00% | +0.47 | 55.0% | 42.6% |
| Ultra Aggressive   | 15 | 10.53% | +0.50 | 70.0% | 40.5% |

### 3. Per-altcoin underlying coverage
27 distinct altcoin / equity-proxy underlyings now in the universe:
AAVE, ADA, AVAX, BAT, DOGE, DOT, ENA, FIL, HBAR, HYPE, LINK, LTC,
MANA, NEAR, PENGU, SOL, STRK, SUI, TAO, TON, TRX, UNI, XLM, XRP, ZEC,
plus IBIT / ETHA (used by ETF-of-ETF wrappers, route to BTC/ETH long-
run CAGR via `_underlying_cagr()`).

Every altcoin underlying has a yfinance mapping in
`_ALTCOIN_YFINANCE_TICKER` so per-coin CAGR is used (no uniform haircut
bias). Fallback to BTC × 0.70 only fires when yfinance has no per-coin
history, with explicit basis-string tagging.

### 4. Auto-classify decision boundary
`core/etf_review_queue._auto_classify` correctly routes:
- High-confidence BTC / ETH / SOL filings → `auto_approve` (universe
  picks up via additions sidecar)
- Off-topic filings → `auto_reject` (won't re-flag on next scan)
- Ambiguous (missing ticker but crypto markers present) → `pending`
  (FA review surface in Settings)

5/5 test cases pass.

### 5. recalculate_all_portfolios end-to-end
- Universe size: 211
- Clients computed: 3 / 3 (zero errors)
- Beatrice Chen (Ultra Conservative): 9 holdings, Sharpe +0.31
- Marcus Avery (Moderate): 9 holdings, Sharpe +0.02
- Priya Patel (Ultra Aggressive): 15 holdings, Sharpe -0.04
- Snapshot persists to `data/portfolio_snapshot.json`

### 6. Daily-scanner cron + workflow
- `cron: "0 13 * * *"` confirmed = 9 AM EST
- Workflow calls `recalculate_all_portfolios` after `daily_scanner`
- `EDGAR_CONTACT_EMAIL` secret reference present
- Workflow auto-commits both `data/scanner_health.json` AND
  `data/etf_review_queue.json` AND `data/portfolio_snapshot.json`
  back to repo via `git status --porcelain data/` catch-all

### 7. Cornish-Fisher constants (per CLAUDE.md §9)
- `_CF_DEFAULT_SKEW = -0.7` ✓ (crypto midpoint per Shahzad et al. 2022)
- `_CF_DEFAULT_KURT = 8.0` ✓ (crypto midpoint per Chaim & Laurini 2018)
- Maillard (2012) caps: S ∈ [-1.5, +1.5], K ∈ [0, 15] ✓
- MDD factor `f` is Sharpe-dependent per Magdon-Ismail 2004 ✓

### 8. Pytest suite
**262 / 262 passing.** No regressions vs round-2 baseline.

### 9. Prod verifier
**9 / 9 passing** against https://etf-advisor.streamlit.app/.

## Verdict

**No code changes required.** Universe-211 + auto-approval + auto-
rebalance landed cleanly. Math fairness + tier construction + scanner
wiring + math constants all match audit-round-2's clean baseline.

Tag at this state: `universe-211-etfs-2026-04-27`.

## Files referenced

- `data/etf_universe.json` — 211 ETFs / 39 issuers / 10 categories
- `core/etf_review_queue.py` — auto-classifier
- `core/scheduler.py` — auto-rebalance
- `core/portfolio_engine.py` — build_portfolio + math
- `integrations/data_feeds.py` — per-altcoin CAGR map
- `.github/workflows/daily_scanner.yml` — 9 AM EST cron
- `docs/math_audit_round_2_2026-04-26.md` — round-2 baseline
- `docs/math_audit_round_3_2026-04-27.md` — this file
