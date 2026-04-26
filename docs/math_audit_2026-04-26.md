# Math audit — 2026-04-26

Per-altcoin fairness audit + fix. User concern: "I don't believe there is a
true and fair breakdown of all the ETFs available, especially those that
are considered to be alt coins."

## Scope

Six audit areas, in priority order:

1. **Forward-return formula per category** (`integrations/data_feeds.py::get_forward_return_estimate`)
2. **Issuer-tier nudge** (`core/portfolio_engine.py::_issuer_tier_nudge`)
3. **Risk-metrics calibration** (Cornish-Fisher S/K, MDD factor)
4. **Composite signal weights** (`core/signal_adapter.py`)
5. **Tier allocation matrix** (`core/risk_tiers.py::TIER_CATEGORY_ALLOCATIONS`)
6. **Per-ETF scoring within category** (selection inside each tier slot)

## 1. Forward-return formula — FIX SHIPPED

### Finding

Pre-2026-04-26: `category == "altcoin_spot"` modeled every coin identically:

```python
fwd = btc_cagr * 0.70 - er_drag * 100.0
```

Same haircut applied to SOL, XRP, LTC, DOGE, HBAR, AVAX, ADA. **Not fair.**
Per-coin track records vary widely:

| Coin | Approx. 5yr CAGR (yfinance, indicative) |
|---|---|
| SOL  | strong (~+50% / yr post-2020)  |
| XRP  | flat to mid (~+5-10% / yr)     |
| LTC  | flat (~+0-5% / yr)             |
| DOGE | high but volatile (~+30% / yr) |
| ADA  | mid (~+15% / yr)               |
| AVAX | mid (~+20% / yr)               |
| HBAR | mid (~+10% / yr)               |

Burying these into a single BTC × 0.70 disadvantages SOL and DOGE; flatters
XRP and LTC.

`_underlying_cagr()` (used by leveraged + income_covered_call wrappers ON
altcoin underlyings — SOLT, XRPT, etc.) ALSO defaulted to BTC for any
underlying not explicitly named ETH/BTC/MSTR/COIN/MARA/RIOT. Same bias.

### Fix

Three changes in `integrations/data_feeds.py`:

1. **`_ALTCOIN_YFINANCE_TICKER` map** — explicit per-coin yfinance ticker
   for the 9 altcoin underlyings the universe references (SOL, XRP, LTC,
   DOGE, ADA, AVAX, HBAR, DOT, LINK).

2. **`_altcoin_cagr_or_none(coin)` helper** — fetches the coin's actual
   long-run CAGR via `get_long_run_cagr(<COIN>-USD, period="10y")`. Returns
   `(None, reason)` when yfinance has no data for the symbol so the caller
   can fall back transparently.

3. **`altcoin_spot` category logic rewritten:**
   - Try `_altcoin_cagr_or_none(underlying)` first.
   - On hit: use the per-coin CAGR with **no haircut** (the data already
     encodes every drawdown the coin has experienced; a uniform haircut
     would double-count). Basis string: `"SOL-USD long-run CAGR (X%) — per-coin (no uniform haircut), minus expenses"`.
   - On miss: fall back to BTC × 0.70 with the basis string explicitly
     noting `"fallback — <coin> history unavailable"`. Newer launches
     (e.g., 2026-launched altcoin trusts) take this path until enough
     history accrues.

4. **`_underlying_cagr()` extended** — now resolves altcoin underlyings
   through the same per-coin path. Affects leveraged altcoin wrappers
   (SOLT, XRPT) and any future income_covered_call ALT wrappers.

### Impact

For an FA running the Portfolio page on Tier 4 / Tier 5 baskets that
include altcoin_spot allocation:

- **SOL ETFs (BSOL, FSOL, SSOL, QSOL, GSOL):** Forward estimate ≈ SOL's
  actual CAGR (10yr) instead of BTC × 0.70. Higher when SOL has
  outperformed BTC, lower when it has not. Either way: data-grounded.
- **XRP / LTC ETFs:** Forward estimate now reflects each coin's slow
  long-run track record, not an inflated BTC × 0.70.
- **DOGE / HBAR / AVAX / ADA ETFs:** Same — coin-specific.
- **Newer-launch altcoin spot ETFs without yfinance coverage:** explicit
  fallback to BTC × 0.70 + visible "fallback" tag in the basis tooltip.

The ETF Detail page's "Forward estimate" tile already surfaces the basis
string, so the FA can see which path was used per ETF.

### What to verify post-deploy

Open ETF Detail for at least one ETF in each underlying. Hover the
Forward-estimate tile or read the metadata caption. Confirm:
  - SOL ETFs say "SOL-USD long-run CAGR ..."
  - LTC ETFs say "LTC-USD long-run CAGR ..."
  - Newer alt spots (HBR, CDOG, ADAX) may say "BTC-USD ... (fallback)" if
    yfinance hasn't indexed the underlying yet.

## 2. Issuer-tier nudge — NO FIX NEEDED (audit clean)

`core/portfolio_engine.py::_issuer_tier_nudge`:
- **Tier A (+2pp):** BlackRock iShares, Fidelity. Both issue BTC + ETH
  spot only — no altcoin ETFs. Nudge does not advantage them on alts.
- **Tier C (-2pp):** GBTC (legacy 150bps), ETHE (legacy 250bps), DEFI
  (futures-based, structural Tier C), XRPR (94bps swaps-wrapped 40-Act
  fund — NOT spot), BITW (closed-end during conversion).
- **Tier B (neutral):** everything else, including all altcoin spot
  issuers (Bitwise, Grayscale's altcoin spot trusts, 21Shares, Canary,
  REX-Osprey on spot products).

XRPR is the only altcoin in Tier C, and the reason is structural (40-Act
swaps wrapper, not spot). Documented + defensible. **No bias.**

## 3. Risk-metrics calibration — FLAG, NO FIX (post-demo)

Cornish-Fisher VaR uses S=−0.25, K=2.5 — retuned for crypto-ETF from RWA
defaults during Phase-2 calibration. MDD factor 2.7.

These were calibrated on a BTC + ETH price series. Altcoins typically
exhibit **higher kurtosis (fatter tails)** and **larger MDD factors**
than BTC. Applying the BTC-tuned numbers to altcoin baskets likely
under-estimates VaR + MDD for high-altcoin tiers.

**Recommended post-demo work:** re-fit S, K, and MDD factor on a
proper holdout with at least 3 years of altcoin price history. Until
then, the current numbers are conservative for Tiers 1-3 (BTC/ETH heavy)
and slightly optimistic for Tiers 4-5 (altcoin heavy).

Flagged in `pending_work.md` under post-demo backlog.

## 4. Composite signal weights — NO FIX NEEDED

`core/signal_adapter.py`: 0.45·RSI + 0.35·MACD + 0.20·Momentum.
Derived from crypto-signal-app and applied to ETF price series.

Same indicator math works on any spot price series — RSI / MACD /
momentum are scale-free + asset-agnostic. The thresholds (BUY ≥ +0.30,
SELL ≤ −0.30) are conservative enough that altcoin noise doesn't
trigger more false signals than BTC. No structural bias against alts.

## 5. Tier allocation matrix — NO FIX NEEDED

`core/risk_tiers.py::TIER_CATEGORY_ALLOCATIONS`. Tier 1 (Ultra
Conservative) excludes altcoin_spot entirely (correct — retiree clients).
Tier 2 (Conservative) includes a small altcoin allocation. Tiers 3-5
include progressively larger altcoin allocations. This matches the
"more aggressive = more diversification across alts" principle.

Verified: altcoin_spot allocation is allowed up to substantial weight
in higher tiers; no implicit cap that disadvantages alts.

## 6. Per-ETF scoring within category — FLAG, NO FIX (post-demo)

Selection inside each category slot is sorted by expense ratio (lower
fee wins). Tiebreaker: issuer-diversity (don't load 3 BlackRock funds
into a Tier 3 BTC slot if 1 is enough).

**Possible alt-bias:** newer altcoin ETFs sometimes have higher fees
than BTC/ETH spots (alts: 35-94 bps; BTC spots: 19-25 bps). Within the
altcoin_spot category itself this is fair (compare alts to alts), but
across categories it could deweight alts in mixed-category tier slots.

Mitigation already in place: tier allocation matrix sets per-category
target weights — selection within category competes alts vs alts only.
So the expense-ratio sort is FAIR within its slot.

**Recommended post-demo work:** add an AUM tiebreaker (prefer larger
funds for liquidity) so that within-category sorting isn't
fee-only-dominated. Adds nuance without changing the basic fairness.

## Summary

| Area | Verdict | Action |
|---|---|---|
| Forward-return formula | UNFAIR — uniform 0.70 haircut | **FIXED** this commit |
| Issuer-tier nudge | Fair | None needed |
| Risk-metrics calibration | Flagged (BTC-tuned, alt under-estimate) | Post-demo |
| Composite signal weights | Fair | None needed |
| Tier allocation matrix | Fair | None needed |
| Per-ETF scoring | Fair within slot, flagged for AUM tiebreaker | Post-demo |

## Files changed in this commit

- `integrations/data_feeds.py` — per-altcoin CAGR lookup + altcoin_spot
  category logic rewrite (no uniform haircut; transparent fallback
  basis string).
- `docs/math_audit_2026-04-26.md` — this file.
