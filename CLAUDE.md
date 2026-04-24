# Claude Code — Master Agreement
# ETF ADVISOR PLATFORM
# Last updated: 2026-04-23
# Inherits from: ../master-template/CLAUDE_master_template.md

> This file overrides or extends the master template where noted.
> This project is the most detailed of the four apps — it has a
> Friday-deadline demo and several demo-specific constraints in
> Section 22.

---

## SECTION 1 — PERMISSION & AUTONOMY

[VERBATIM FROM MASTER TEMPLATE.]

---

## SECTION 2 — PROJECT SCOPE

```
  Name:          ETF Advisor Platform
  Path:          C:\Users\david\OneDrive\Desktop\Cowork\etf-advisor-platform
  Repo:          github.com/davidduraesdd1-blip/etf-advisor-platform (PRIVATE)
  Deploy:        https://etf-advisor.streamlit.app/  (private app until post-demo review)
  User role:     builder / designer / reviewer (primary), partner joining mid-week
  Collaborators: 2 (user + partner). Partner codebase integrates separately.

  Purpose: FA-facing (financial advisor) portfolio platform for crypto ETFs.
  Designed around two personas: Quick Executor (80% of use; 2-click basket
  execution) and Deep Diver (20%; drills into backtests, ETF composition,
  underlying coin research).

  Primary framing (Framing A): ETF-only product. Single-asset-class focus.
  Optional framing (Framing B): Extended platform covering ETF + RWA + DeFi,
  enabled via EXTENDED_MODULES_ENABLED feature flag in config.py. Off by default.

  Foundation codebases (READ ONLY — import patterns, do not modify):
    - crypto-signal-app  → Signal engine; composite_signal.py, cycle_indicators.py
    - rwa-infinity-model → Portfolio construction engine; portfolio.py gold reference
    - flare-defi-model   → Cleanest directory structure; architectural template

  Integration with parent FA platform: proprietary, not named here, integrates
  at the advisor workflow layer post-demo. Design UI to feel like a module of
  a larger platform, not a standalone product.
```

---

## SECTION 3 — COMMIT & PUSH RULES

[VERBATIM FROM MASTER TEMPLATE.]

---

## SECTION 4 — UNIFIED AUDIT & TEST PROTOCOL

[VERBATIM FROM MASTER TEMPLATE — no project-specific overrides.]

---

## SECTION 5 — RESEARCH STANDARDS

[VERBATIM FROM MASTER TEMPLATE.]

---

## SECTION 6 — BRANDING & IDENTITY

BRAND_NAME and BRAND_LOGO_PATH config constants exist in config.py.
When unset: render a clean professional placeholder header.

For demo: `BRAND_NAME = "ETF Advisor Platform"` (placeholder — real
brand decided post-demo with partner).

A full rebrand must be no more than 2 line changes.

---

## SECTION 7 — USER LEVEL SYSTEM

[MASTER TEMPLATE with project-specific tier definitions:]

  Beginner advisor:   new FAs, FAs new to crypto, compliance reviewers
                      → Plain English. No crypto jargon. "This portfolio
                        invests in Bitcoin funds" not "allocation to spot BTC ETFs."
                      → Every signal paired with "What this means for your client"
                      → Execute button shows full confirmation modal with
                        "you are about to..." language
  Intermediate:       FAs with 6+ months crypto exposure
                      → Condensed signal interpretations
                      → Full charts visible
                      → Execute button skips the "you are about to" modal
                        (still shows order summary)
  Advanced:           FAs with deep crypto allocation experience
                      → Raw Sharpe/Sortino/Calmar visible alongside plain-English
                      → All Monte Carlo distributions accessible
                      → Tooltips collapsed by default
                      → Indicator panels show raw values (RSI=73, not just "overbought")

---

## SECTION 8 — DESIGN STANDARDS

[MASTER TEMPLATE with project-specific calibrations:]

PROJECT-SPECIFIC TONE NOTES:
- This is a financial advisor tool. Tone is calmer and more institutional
  than the crypto-signal-app. Red and green used sparingly, never for
  decoration. A drawdown number in red is a fact; an entire panel in red
  is alarmist and wrong.
- Backtest performance displays ALWAYS include: multiple time horizons
  (1Y, 3Y, 5Y, since-inception), benchmark comparison, max drawdown,
  "Hypothetical results" disclaimer, and a link to methodology. This is a
  compliance requirement for advisor-facing performance presentations (SEC
  Marketing Rule). Non-negotiable.
- "Execute basket" button copy NEVER says just "Buy." Use "Execute basket"
  or "Send to [Broker]". Compliance language matters here.
- Bad-day scenarios (portfolio in drawdown): calm messaging. Prompt to
  "review risk tolerance and time horizon" not "sell now." No red flashing
  alarms.

---

## SECTION 9 — MATH MODEL ARCHITECTURE

LAYER 1 — TECHNICAL: coin-level indicators from crypto-signal-app adapted
  to ETF-level signals. Volume, momentum, trend. Weighted across ETF
  holdings for the ETF-level composite.

LAYER 2 — MACRO / FUNDAMENTAL: rates, DXY, VIX, liquidity conditions.
  Crypto ETFs are rate-sensitive (risk-on asset class). Macro context is
  a primary input, not optional.

LAYER 3 — SENTIMENT: crypto Fear & Greed, BTC funding rates, ETF flow data
  (from SoSoValue or similar free source), news sentiment.

LAYER 4 — ON-CHAIN: MVRV, SOPR, active addresses, exchange flows, TVL.
  Applies to the underlying coins held by each ETF, aggregated to ETF level
  by holding weight.

PORTFOLIO LAYER — the adapted portfolio.py from rwa-infinity-model. Takes
  a risk tier, selects from the ETF universe, returns holdings with weights
  and full risk metrics (Sharpe, Sortino, Calmar, VaR, CVaR, Monte Carlo).

OUTPUT RULE: ONE clear signal per ETF: BUY / HOLD / SELL with shape encoding.

---

## SECTION 10 — DATA SOURCES & FALLBACK CHAINS

ETF reference data (list, holdings, AUM, expense ratio, inception):
  Primary:   SEC EDGAR N-PORT filings (free, authoritative, slow)
  Secondary: ETF.com public pages (scraping, respect robots.txt)
  Tertiary:  issuer sites (BlackRock, Fidelity, VanEck, etc.) per-ETF
  Upgrade path: paid API (ETF.com API, YCharts, Morningstar) — one-line flip

ETF price data (OHLCV, intraday):
  Primary:   Yahoo Finance via yfinance (free, known rate limits, cache aggressively)
  Secondary: Alpha Vantage free tier (25 req/day, use for specific lookups only)
  Tertiary:  Stooq (free, slightly delayed)
  Upgrade path: Polygon.io Stocks Starter ($29/mo), IEX Cloud, Finnhub

ETF daily scanner (new ETFs coming to market):
  Primary:   SEC EDGAR RSS feed for N-1A and 497 filings tagged "cryptocurrency"
  Secondary: cryptorank.io /news/tag/spot-bitcoin-etf-flows and adjacent tags
  Tertiary:  CoinDesk / The Block crypto-ETF news feeds (RSS)

ETF flow data (daily net inflows / outflows per ETF — Layer 3 sentiment input):
  Primary:   cryptorank.io ETF-flow endpoints (cumulative + daily per-issuer breakdown)
  Secondary: SoSoValue.xyz ETF dashboard (scraping or public API when available)
  Tertiary:  Farside Investors CSV exports (daily BTC + ETH ETF flows)
  Upgrade path: Bloomberg Terminal feed (enterprise only)

Underlying coin data (for LAYER 4 on-chain):
  Already solved in crypto-signal-app — reuse those data_feeds.py patterns.
  OKX → CoinGecko → Kraken chain per master template Section 10.

---

## SECTION 11 — DEPLOYMENT ENVIRONMENTS

[MASTER TEMPLATE with project-specific:]

Environments:
  Local:     Windows dev, VS Code + Claude Code extension
  Cloud:     Streamlit Cloud private app (demo and beyond)

Shared modules with foundation repos: NONE at runtime. We copy code patterns
and full modules into this repo, but this repo does not import from
crypto-signal-app, rwa-infinity-model, or flare-defi-model. This is by user
design: foundation repos stay frozen as reference implementations.

Streamlit Cloud URL: https://etf-advisor.streamlit.app/

---

## SECTION 12 — DATA REFRESH RATES

[MASTER TEMPLATE. Project-specific refresh windows:]

Dashboard client statuses:     1 minute cache
ETF prices (intraday):         5 minute cache during market hours,
                               60 minute cache off-hours and weekends
ETF holdings data:             24 hour cache (changes slowly, rarely intraday)
Portfolio construction output: 10 minute cache keyed on tier+universe hash
Monte Carlo results:           10 minute cache per master template pattern
Daily ETF scanner:             Runs once per trading day at 16:30 ET
Monitoring engine signals:     15 minute recompute cycle

---

## SECTION 13 — DATA UNIVERSE

CRYPTO ETF UNIVERSE (starting list, ~20 ETFs, expands via daily scanner):

Spot Bitcoin ETFs (US-listed):
  IBIT, FBTC, BITB, ARKB, BTCO, EZBC, BRRR, HODL, BTC, GBTC, DEFI

Spot Ethereum ETFs (US-listed):
  ETHA, FETH, ETHE, ETHW, CETH, QETH, EZET, ETH

Multi-asset / thematic crypto ETFs: scanner-populated.

RISK TIERS (5-tier, recalibrated allocations for ETFs):
  Tier 1 — Ultra Conservative:   5% crypto ETF exposure ceiling
  Tier 2 — Conservative:         10% ceiling
  Tier 3 — Moderate:             20% ceiling
  Tier 4 — Aggressive:           35% ceiling
  Tier 5 — Ultra Aggressive:     50%+ ceiling

Ceiling percentages describe the crypto-ETF portion of a client's TOTAL
portfolio — not the 100% of what this tool constructs.

EXTENDED MODULES (when EXTENDED_MODULES_ENABLED=True):
  RWA module:  57 assets from rwa-infinity-model's RWA_UNIVERSE
  DeFi module: Flare blockchain DeFi positions from flare-defi-model

---

## SECTION 14 — BACKUP & RESTORE PROTOCOL

[VERBATIM FROM MASTER TEMPLATE.]

---

## SECTION 15 — SPRINT TASK LIST

[VERBATIM FROM MASTER TEMPLATE.]

---

## SECTION 16 — SESSION CONTINUITY & RESUME PROTOCOL

[VERBATIM FROM MASTER TEMPLATE.]

---

## SECTION 17 — PARALLEL AGENT MONITORING & TAKEOVER

[VERBATIM FROM MASTER TEMPLATE.]

---

## SECTION 18 — STREAMLIT-SPECIFIC PATTERNS

[VERBATIM FROM MASTER TEMPLATE.]

---

## SECTION 19 — CROSS-APP MODULE DISCIPLINE

[MASTER TEMPLATE. Project application:]

This project does NOT share modules with the three foundation repos at runtime
(per Section 11). BYTE-IDENTICAL cross-app constraint does not apply externally.

It DOES apply internally: if this project ships multiple apps (admin panel,
advisor app, compliance view), shared modules between them are byte-identical
per master template.

---

## SECTION 20 — GIT HYGIENE ON SHARED DEV MACHINES

[VERBATIM FROM MASTER TEMPLATE.]

---

## SECTION 21 — TONE & STYLE DURING COLLABORATION

[VERBATIM FROM MASTER TEMPLATE.]

Key reminder for this project specifically: user is moving fast toward a
client demo. Push back on premature optimization. Ship the 80% that
demonstrates the vision; defer the 20% that only matters at scale.

---

## SECTION 22 — PROJECT-SPECIFIC DEMO CONSTRAINTS

For the Friday-soft-deadline demo:

1. Demo mode flag: `DEMO_MODE = True` in config.py seeds 3 realistic client
   profiles and prebuilt portfolios. Allows the app to demo without live
   internet if Wi-Fi fails at the client site.

2. Mock broker integration: `BROKER_PROVIDER = "mock"` in config.py.
   "Execute basket" shows a realistic confirmation modal but does not hit
   any broker API. Post-demo we swap to `BROKER_PROVIDER = "alpaca_paper"`
   for a real sandbox integration, and later `"alpaca"` for live.

3. No real client data in the repo or in demo fixtures. Every client shown
   is fictional, explicitly labeled as a demo persona. Compliance requirement.

4. Extended modules (RWA/DeFi) preview: visible only when
   EXTENDED_MODULES_ENABLED=True in config.py. Banner on both modules reads
   "Extended coverage — preview release. Execution not yet enabled for this
   module."

5. Every backtest or performance display must include: multiple time
   horizons, benchmark comparison (default: 60/40 blended index + BTC), max
   drawdown, "Hypothetical results" disclaimer, link to methodology page.

6. Methodology page (`pages/98_methodology.py`) exists by Friday with
   placeholder copy. Replaced with full content post-demo.

---

## SECTION 23 — TOKEN EFFICIENCY (PROGRESS-PRESERVING)

[VERBATIM FROM MASTER TEMPLATE.]

---

## SECTION 24 — POST-CHANGE FULL REVIEW PROTOCOL (WHEN)

[VERBATIM FROM MASTER TEMPLATE.]

Project-specific notes:
- Fast-test suite target: under 30s locally.
- Hot paths for perf check: the landing page, the portfolio construction
  page, the basket-execute flow.
- Pre-push hook runs Part A of Section 25 against the live Streamlit URL.

---

## SECTION 25 — DEPLOYMENT VERIFICATION PROTOCOL

[VERBATIM FROM MASTER TEMPLATE.]

Project-specific:
- Deploy URL: https://etf-advisor.streamlit.app/
- Checklist: `shared-docs/deployment-checklists/etf-advisor-platform.md`
- Fallback-chain test: swap `YAHOO_FINANCE` in `.env` to an invalid URL
  and confirm Alpha Vantage secondary takes over.
- Compliance check on EVERY deploy: verify all performance displays
  still show time horizons, benchmark, max drawdown, hypothetical
  disclaimer, and methodology link.
- Demo-mode check: `DEMO_MODE=True` boots without internet, all 3
  demo personas visible.
