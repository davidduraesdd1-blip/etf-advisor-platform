# Claude Code — Master Agreement
# ETF ADVISOR PLATFORM
# Last updated: 2026-04-20
# Inherits from: user's master CLAUDE.md template (all unmodified sections verbatim)

---

## SECTION 1 — PERMISSION & AUTONOMY

Claude has 100% full permission to complete all coding, research, engineering,
testing, optimization, auditing, committing, and pushing tasks as quickly and
efficiently as possible. This includes all file edits, writes, reads, git
commits, git pushes, bash commands, and tool use.

No permission prompts. No mid-task check-ins. Ever.

One pause point only: before implementing any upgrade or new feature, present
a numbered proposal list and wait for approval. Once approved, execute the
entire list autonomously with no further check-ins until done.

Claude may ask clarifying questions on direction-level decisions only — never
guess on those. The user may ask clarifying questions at any time and Claude
must answer clearly before continuing.

---

## SECTION 2 — PROJECT SCOPE

  Name:          ETF Advisor Platform
  Path:          [SET ON LOCAL CLONE — typically C:\Users\[user]\Projects\etf-advisor-platform]
  Repo:          github.com/davidduraesdd1-blip/etf-advisor-platform (PRIVATE)
  Deploy:        Streamlit Cloud (private app until post-demo review)
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
    - github.com/davidduraesdd1-blip/crypto-signal-app
      → Signal engine; coin-level indicators, Buy/Hold/Sell logic,
        composite_signal.py, cycle_indicators.py, top_bottom_detector.py
    - github.com/davidduraesdd1-blip/rwa-infinity-model
      → Portfolio construction engine; 5-tier risk structure; Monte Carlo;
        Modern Portfolio Theory; VaR/CVaR; portfolio.py is the gold reference
    - github.com/davidduraesdd1-blip/flare-defi-model
      → Cleanest directory structure (pages/, ui/, agents/, scanners/, models/);
        use as the architectural template for this project

  Integration with parent FA platform: proprietary, not named here, integrates
  at the advisor workflow layer post-demo. Design UI to feel like a module of
  a larger platform, not a standalone product.

---

## SECTION 3 — COMMIT & PUSH RULES

Every completed unit of work must be committed and pushed to GitHub
immediately. No exceptions. No half-committed states.

Rules:
- Never amend published commits. Always create new commits forward.
- Commit messages include a full written report of every change, bug fix,
  and optimization. The "why" beats the "what."
- End every commit with:
    Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
- Never skip hooks (--no-verify) unless explicitly asked.
- Never force-push to main.
- Stage files by name, never `git add -A` or `git add .` (can accidentally
  include .env or secrets).
- If a pre-commit hook fails, fix the issue and create a NEW commit — never
  amend the failed one.

---

## SECTION 4 — UNIFIED AUDIT & TEST PROTOCOL

[VERBATIM FROM MASTER TEMPLATE — no project-specific overrides.]

Every audit pass covers:
- Every source file: bugs, logic errors, edge cases, crashes, security holes,
  performance bottlenecks, UX text errors, wrong calculations, missing error
  handling, dead code, redundant API calls, memory leaks, blocking operations,
  cache misses, slow queries — from typo to logic failure.
- Every feature: every button, scan, data feed, chart, API call, fallback,
  error path, page, tab, modal, dropdown.
- Every calculation verified against known correct values.
- Every user level: Beginner, Intermediate, Advanced.
- Every theme: dark mode and light mode.
- All refresh intervals verified operating within API rate limits.

Fix issues immediately as found, before moving to the next file.
Record every issue: file, line, severity, description, fix applied.

Audit frequency — tiered:
  Any file touched            → Audit that file + all direct dependencies
  Any sprint item completed   → Full audit of affected area
  Before any new sprint       → Full pre-sprint audit
  Monthly                     → Full deep audit
  Any time user requests      → Whatever scope user specifies

Written report required after every audit, included in the commit message.

---

## SECTION 5 — RESEARCH STANDARDS

[VERBATIM FROM MASTER TEMPLATE.]

Research is mandatory before any upgrade/new feature proposal, any new
dependency, or any time the user explicitly requests research.

Minimum 30 distinct sources per proposal. Cover official docs, third-party
blogs, Stack Overflow, competitor products, social media, regulatory bodies,
academic preprints where applicable.

Output must state: what competitors do that we don't, market gaps,
version numbers and release dates.

---

## SECTION 6 — BRANDING & IDENTITY

BRAND_NAME and BRAND_LOGO_PATH config constants exist in config.py.
When unset: render a clean professional placeholder header.

For demo: BRAND_NAME = "ETF Advisor Platform" (placeholder — real brand
decided post-demo with partner).

A full rebrand must be no more than 2 line changes. All copy, headers, and
logo references pull from config constants. No hardcoded brand strings
anywhere in the app code.

---

## SECTION 7 — USER LEVEL SYSTEM

Three tiers: Beginner / Intermediate / Advanced, applied identically across
every screen. Beginner is the default for first-time visits.

THIS PROJECT'S SPECIFICS:
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

Level selector always visible in sidebar. Level persists in session state.
Glossary depth, chart complexity, signal explanations, and error messages
all scale with level.

---

## SECTION 8 — DESIGN STANDARDS

[MASTER TEMPLATE with project-specific calibrations noted.]

Colors (from master template — unchanged):
  Primary:    #00d4aa  (teal)
  Success:    #22c55e  (green)
  Danger:     #ef4444  (red)
  Warning:    #f59e0b  (amber)
  Dark bg:    #0d0e14
  Dark card:  #111827
  Light bg:   #f1f5f9
  Light card: #ffffff

Typography, clamp() floors, theme toggling, accessibility, error messages,
UI consistency, formatting specifics: ALL per master template.

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
  alarms. This is per user's direct guidance from the planning conversation.

---

## SECTION 9 — MATH MODEL ARCHITECTURE

[MASTER TEMPLATE layered pattern, with this project's layer assignments:]

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

OUTPUT RULE (master template): however many layers feed in, the output is
ONE clear signal per ETF: BUY / HOLD / SELL with shape encoding.

---

## SECTION 10 — DATA SOURCES & FALLBACK CHAINS

[MASTER TEMPLATE rules apply. Project-specific source chains below:]

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
  Primary:   SEC EDGAR RSS feed for N-1A (new fund registration) and 497
             (prospectus effective) filings tagged "cryptocurrency"
  Secondary: CoinDesk / The Block crypto-ETF news feeds (RSS)
  Tertiary:  Manual periodic review

Underlying coin data (for LAYER 4 on-chain):
  Already solved in crypto-signal-app — reuse those data_feeds.py patterns.
  OKX → CoinGecko → Kraken chain per master template Section 10.

CRITICAL per master template:
- Test every source against Streamlit Cloud (non-residential IP) before
  committing to it as primary. CoinMetrics, Binance US, Bybit all have
  datacenter-IP quirks documented in the master template.
- Cache empty results with 30s TTL; successful results per source's natural
  cadence.
- Never silently fall back to plain text when binary expected.

---

## SECTION 11 — DEPLOYMENT ENVIRONMENTS

[MASTER TEMPLATE with project-specific:]

Environments:
  Local:     Windows dev, VS Code + Claude Code extension
  Cloud:     Streamlit Cloud private app (demo and beyond)

Detection: use __file__-relative paths per master template Section 11.

Shared modules with foundation repos: NONE at runtime. We copy code patterns
and full modules into this repo, but this repo does not import from
crypto-signal-app, rwa-infinity-model, or flare-defi-model. This is by user
design: foundation repos stay frozen as reference implementations.

Secrets: Streamlit Cloud Secrets UI only. Never in repo. .env.example shows
the expected variables; actual .env is gitignored per master template.

---

## SECTION 12 — DATA REFRESH RATES

[MASTER TEMPLATE. Project-specific refresh windows:]

Dashboard client statuses:     1 minute cache (near-real-time feel)
ETF prices (intraday):         5 minute cache during market hours,
                               60 minute cache off-hours and weekends
ETF holdings data:             24 hour cache (changes slowly, rarely intraday)
Portfolio construction output: 10 minute cache keyed on tier+universe hash
Monte Carlo results:           10 minute cache per master template pattern
Daily ETF scanner:             Runs once per trading day at 16:30 ET
                               (after US close, before next open)
Monitoring engine signals:     15 minute recompute cycle

All within free API limits. Paid-tier upgrade is a config flip per master
template Section 12.

"Refresh All Data" button visible in sidebar on every page. Forces cache bypass.

---

## SECTION 13 — DATA UNIVERSE

CRYPTO ETF UNIVERSE (starting list, ~20 ETFs, expands via daily scanner):

Spot Bitcoin ETFs (US-listed):
  IBIT  — iShares Bitcoin Trust (BlackRock)
  FBTC  — Fidelity Wise Origin Bitcoin Fund
  BITB  — Bitwise Bitcoin ETF
  ARKB  — ARK 21Shares Bitcoin ETF
  BTCO  — Invesco Galaxy Bitcoin ETF
  EZBC  — Franklin Bitcoin ETF
  BRRR  — Valkyrie Bitcoin Fund
  HODL  — VanEck Bitcoin Trust
  BTC   — Grayscale Bitcoin Mini Trust
  GBTC  — Grayscale Bitcoin Trust (legacy, higher fee)
  DEFI  — Hashdex Bitcoin Futures ETF (futures-based comparison)

Spot Ethereum ETFs (US-listed):
  ETHA  — iShares Ethereum Trust (BlackRock)
  FETH  — Fidelity Ethereum Fund
  ETHE  — Grayscale Ethereum Trust
  ETHW  — Bitwise Ethereum ETF
  CETH  — 21Shares Core Ethereum ETF
  QETH  — Invesco Galaxy Ethereum ETF
  EZET  — Franklin Ethereum ETF
  ETH   — Grayscale Ethereum Mini Trust

Multi-asset / thematic crypto ETFs (if available in US markets):
  [Scanner-populated. Solana ETFs pending approval. Basket ETFs emerging.]

RISK TIERS (inherited from rwa-infinity-model, same names, recalibrated allocations for ETFs):

  Tier 1 — Ultra Conservative: 5% crypto ETF exposure ceiling
           Primary: BTC spot ETFs with lowest expense ratios (IBIT, FBTC, BITB)
           Rebalance: quarterly
           Typical client: retiree, client explicitly risk-averse

  Tier 2 — Conservative: 10% ceiling
           Primary: BTC-heavy mix with ETH allocation starting
           Rebalance: quarterly
           Typical client: near-retirement, diversification allocation

  Tier 3 — Moderate: 20% ceiling
           Primary: balanced BTC/ETH, exposure to multiple ETFs for diversification
           Rebalance: bi-monthly
           Typical client: mid-career, moderate risk tolerance

  Tier 4 — Aggressive: 35% ceiling
           Primary: BTC + ETH + emerging thematic ETFs as they become available
           Rebalance: monthly
           Typical client: younger, higher risk tolerance, longer horizon

  Tier 5 — Ultra Aggressive: 50%+ ceiling
           Primary: maximum diversification across all approved crypto ETFs,
                    including higher-expense-ratio issuers for thematic exposure
           Rebalance: bi-weekly
           Typical client: high-conviction crypto allocator, short-to-medium-term lens

These ceiling percentages describe the crypto-ETF portion of a client's TOTAL
portfolio — not the 100% of what this tool constructs. The tool constructs
the crypto-ETF sleeve. The FA decides total-portfolio sizing separately,
guided by a "Crypto allocation within total portfolio" input on the Portfolio
View.

EXTENDED MODULES (when EXTENDED_MODULES_ENABLED=True):
  RWA module: 57 assets from rwa-infinity-model's RWA_UNIVERSE, same 5-tier
              structure, preview banner enabled
  DeFi module: Flare blockchain DeFi positions from flare-defi-model, same
               5-tier structure, preview banner enabled

---

## SECTION 14 — BACKUP & RESTORE PROTOCOL

[MASTER TEMPLATE verbatim.]

Before every major sprint: `git tag backup-pre-[description]-YYYY-MM-DD`
Push tags immediately. These are restore points.

---

## SECTION 15 — SPRINT TASK LIST

[MASTER TEMPLATE verbatim.]

Sprint tasks live in pending_work.md. CLAUDE.md contains permanent standards only.
Sprint approval persists across sessions.

---

## SECTION 16 — SESSION CONTINUITY & RESUME PROTOCOL

[MASTER TEMPLATE verbatim. Standing permission granted.]

On every new session: read MEMORY.md, read pending_work.md, git log -10,
identify resume point, announce briefly, execute.

---

## SECTION 17 — PARALLEL AGENT MONITORING & TAKEOVER

[MASTER TEMPLATE verbatim.]

5-minute monitoring cadence. Takeover on stall. Never wait indefinitely.

---

## SECTION 18 — STREAMLIT-SPECIFIC PATTERNS

[MASTER TEMPLATE verbatim. All 11 hard-learned patterns apply.]

---

## SECTION 19 — CROSS-APP MODULE DISCIPLINE

[MASTER TEMPLATE with this project's application:]

This project does NOT share modules with the three foundation repos at runtime
(per Section 11 of this file). BYTE-IDENTICAL cross-app constraint does not
apply.

It DOES apply internally: if this project later ships multiple apps (admin
panel, advisor app, compliance view), shared modules between them are
byte-identical per master template.

---

## SECTION 20 — GIT HYGIENE ON SHARED DEV MACHINES

[MASTER TEMPLATE verbatim. Windows + OneDrive caveats apply on local dev.]

---

## SECTION 21 — TONE & STYLE DURING COLLABORATION

[MASTER TEMPLATE verbatim.]

Key reminder for this project specifically: user is moving fast toward a
client demo. Push back on premature optimization. Ship the 80% that
demonstrates the vision; defer the 20% that only matters at scale.

---

## SECTION 22 — PROJECT-SPECIFIC DEMO CONSTRAINTS (new section)

For the Friday-soft-deadline demo:

1. Demo mode flag: `DEMO_MODE = True` in config.py seeds 3 realistic client
   profiles and prebuilt portfolios. Allows the app to demo without live
   internet if Wi-Fi fails at the client site.

2. Mock broker integration: `BROKER_PROVIDER = "mock"` in config.py.
   "Execute basket" shows a realistic confirmation modal but does not hit
   any broker API. Post-demo we swap to `BROKER_PROVIDER = "alpaca_paper"`
   for a real sandbox integration, and later `"alpaca"` for live.

3. No real client data in the repo or in demo fixtures. Every client shown
   is fictional, explicitly labeled as a demo persona. This is a compliance
   requirement.

4. Extended modules (RWA/DeFi) preview: visible only when
   EXTENDED_MODULES_ENABLED=True in config.py. Banner on both modules reads
   "Extended coverage — preview release. Execution not yet enabled for this
   module." Per user's direction in the planning conversation.

5. Every backtest or performance display must include: multiple time
   horizons, benchmark comparison (default: 60/40 blended index + BTC), max
   drawdown, "Hypothetical results" disclaimer, link to methodology page.

6. Methodology page (`pages/98_methodology.py`) exists by Friday with
   placeholder copy. Replaced with full content post-demo.
