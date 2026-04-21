# ETF Advisor Platform — Build Package

Produced: Monday, April 20, 2026, evening
Target demo: Friday (soft deadline, not a wall)
Status: Ready to execute starting Tuesday 2026-04-21

## How to use this document

Single source of truth for the week. Commit to repo. Six sections:

1. Repo decision and setup steps
2. A/B feature-flag architecture
3. Day-by-day plan, Tuesday through Friday
4. The new repo's CLAUDE.md (ready to commit)
5. The first Claude Code prompt for the first morning
6. ETF data strategy + demo narrative

Sections 4, 5, and 6 are what you act on. The rest is context.

---

## 1. Repo decision and setup

**Name:** `etf-advisor-platform`

**Reasoning:** "ETF" in the name keeps the primary product story clear;
"platform" (not "screener") lets the B-framing (extended modules for RWA
and DeFi) live comfortably inside the same name.

**Separate repo**, not a fork or branch of the existing three. The three
foundation repos stay untouched as reference implementations. The new repo
imports from them conceptually (copies code patterns, occasionally full
modules) but never depends on them at runtime.

**Visibility:** Private until confident. Advisor/broker/compliance
sensitivity makes this non-negotiable for the demo phase.

---

## 2. A/B framing architecture — the feature flag approach

Single codebase. One toggle. Two stories.

In `config.py`:

```python
# ─── Extended Modules Feature Flag ────────────────────────────────────────────
# When False (default): ETF-only product. Clean, focused, single-asset-class.
#                       Advisors see only the ETF screener. Matches Framing A.
# When True:            Extended coverage. ETF + RWA + DeFi as separate tabs
#                       within the same advisor workflow. Matches Framing B.
#                       RWA and DeFi tabs show a "Preview Release" banner.
EXTENDED_MODULES_ENABLED = False
```

Everywhere downstream:

```python
from config import EXTENDED_MODULES_ENABLED

if EXTENDED_MODULES_ENABLED:
    st.sidebar.radio("Asset class", ["ETFs", "RWA (preview)", "DeFi (preview)"])
else:
    st.sidebar.markdown("**Crypto ETFs**")
```

For the demo: decide per-client which flag state to show. `False` for
conservative clients who want focus, `True` for ambitious clients who
want the full platform vision. Same underlying code.

What "preview" means for RWA and DeFi tabs:

- They work — render real data pulled from the existing rwa-infinity-model
  and flare-defi-model logic, adapted for the advisor UI
- They display a persistent banner: "Extended coverage — preview release.
  Execution not yet enabled for this module."
- The "Execute basket" button is visually present but opens a "Coming
  soon" modal instead of routing to a broker

---

## 3. Day-by-day plan, Tuesday through Friday

### Tuesday (Day 1) — Foundation

**Goal:** Repo live, CLAUDE.md committed, skeleton app running locally with
placeholder screens.

Deliverables end of day:

- Repo created per Section 1
- CLAUDE.md, README.md, .gitignore, .gitleaks.toml, requirements.txt,
  packages.txt, runtime.txt, Dockerfile — all patterned on flare-defi-model
- Directory structure:

```
etf-advisor-platform/
├── CLAUDE.md
├── MEMORY.md
├── pending_work.md
├── README.md
├── app.py
├── config.py
├── pages/
│   ├── 01_Dashboard.py
│   ├── 02_Portfolio.py
│   ├── 03_ETF_Detail.py
│   └── 99_Settings.py
├── core/
│   ├── portfolio_engine.py
│   ├── etf_universe.py
│   ├── risk_tiers.py
│   └── signal_adapter.py
├── integrations/
│   ├── broker_mock.py
│   └── data_feeds.py
├── ui/
│   ├── components.py
│   └── theme.py
├── data/
│   └── .gitkeep
├── tests/
│   └── test_smoke.py
└── docs/
    ├── ETF_Screener_UX_Spec.md
    ├── BUILD_PACKAGE.md
    └── architecture.md
```

- `streamlit run app.py` launches locally, shows skeleton with 4 pages
  clickable but empty
- First commit, first push. Tag: `backup-pre-day2-2026-04-21`

**Risk:** If foundation takes longer than half a day, cut the RWA/DeFi
preview-tab work from Thursday and push to post-demo.

### Wednesday (Day 2) — Portfolio engine + ETF universe

**Goal:** Math layer and data layer. Both running, both tested.

- `core/portfolio_engine.py` adapted from `rwa-infinity-model/portfolio.py`:
  - Same 5-tier structure (Ultra Conservative → Ultra Aggressive)
  - Same `build_portfolio()`, `compute_portfolio_metrics()`, `run_monte_carlo()` signatures
  - Category allocations retuned for ETFs
  - Monte Carlo, VaR, CVaR, Sharpe/Sortino/Calmar all preserved
- `core/etf_universe.py`:
  - Initial static list of ~20 known crypto ETFs
  - Daily scanner function querying public sources for newly listed crypto ETFs
  - Per-ETF data structure with holdings, expense ratio, AUM, inception date
- `integrations/data_feeds.py`:
  - Free-tier data source implementations
  - Fallback chain per CLAUDE.md Section 10
  - Paid-API hooks as one-line config flips
- Unit tests with mock ETF universe — all pass
- Commit, push, tag

**Risk:** ETF data sources are the biggest unknown. If rate-limited or
blocked, fall back to static JSON seed for demo; frame daily scanner as
"activating post-launch."

### Thursday (Day 3) — Advisor UI

**Goal:** Actual advisor-facing screens, wired to real data.

- Dashboard: mock client list, status column, sortable, filterable
- Portfolio view: 5-tier risk selector, allocation pie/bar chart,
  performance panel (1Y/3Y/5Y/since-inception), "Execute basket" CTA
- ETF detail: Buy/Hold/Sell signal, indicator panel, composition
  breakdown, backtest card
- Settings: broker routing (mock), monitoring preferences, auto-execute
  permissions, `EXTENDED_MODULES_ENABLED` toggle
- Full theme per CLAUDE.md Section 8
- Beginner/Intermediate/Advanced selector in sidebar
- Commit, push, tag

**Risk:** Visual day. Day 2 bugs surface here. Budget 1 extra hour.

### Friday (Day 4) — Polish + demo narrative rehearsal

**Goal:** Convert "working prototype" into "convincing demo."

- 3 realistic demo client profiles
- Polished empty/loading/error states per CLAUDE.md Section 8
- Performance disclaimers on every backtest
- Audit log surface
- `DEMO_MODE=True` config flag injects pre-seeded clients
- Extended-modules preview (RWA + DeFi tabs) if time permits
- Deploy to Streamlit Cloud as private app
- Full audit pass per CLAUDE.md Section 4
- Commit, push, tag `backup-demo-ready-2026-04-24`

**Risk:** Saturday/Sunday buffer if Friday slips.

---

## 4. CLAUDE.md for the new repo

See repo root [`CLAUDE.md`](../CLAUDE.md) for the full 22-section master
agreement for this project. Committed 2026-04-21 as `8330bce`.

---

## 5. First Claude Code prompt

See inside the planning conversation. Day-1 execution started 2026-04-21.

---

## 6. ETF data strategy, free-tier compliant

### Sources, in fallback order

**ETF reference data (holdings, AUM, expense ratio):**

1. SEC EDGAR — free, authoritative, 10 req/sec limit. Every US-listed ETF
   files N-PORT (holdings quarterly) and N-1A (registration). Crypto ETF
   N-PORT data is 3-4 months lagged but reliable. Cache monthly.
2. Issuer sites — BlackRock, Fidelity, VanEck, etc. publish daily holdings
   on ETF pages. Free, no stated rate limit. Scrape politely (1 req/sec,
   robots.txt, User-Agent identified).
3. ETF.com — free public pages. Scraping, same politeness. AUM/expense
   ratios can lag issuer direct by a day.

**ETF price data (OHLCV):**

1. Yahoo Finance via `yfinance` — free, de-facto standard, occasionally
   breaks with rebrands. Cache 5-min TTL during market hours.
2. Alpha Vantage free tier — 25 req/day, 5/min. Spot-check fallback only.
3. Stooq — free, ~15-min delayed, reliable.

**Daily scanner (new ETF detection):**

Key insight: new crypto ETFs go through SEC approval. Every new fund files
on EDGAR.

1. EDGAR full-text search for filings in the last 24h where:
   - Form type is N-1A, 497, or S-1
   - Text contains "cryptocurrency" OR "bitcoin" OR "ethereum" OR "digital asset"
2. Cross-reference filer against existing universe
3. Flag new entrants; auto-add after eligibility check

Runs once per trading day at 16:30 ET. Zero API budget cost.

### Paid upgrade hooks (one-line flips)

```python
# config.py
ETF_REFERENCE_SOURCE = "edgar"        # alternatives: "etfcom_api", "morningstar"
ETF_PRICE_SOURCE     = "yfinance"     # alternatives: "polygon", "iex", "finnhub"

POLYGON_API_KEY      = None
ETFCOM_API_KEY       = None
```

### Rate limit safety

- EDGAR: hard cap 10 req/sec globally. Token bucket in `data_feeds.py`.
- yfinance: cache 5-min TTL market hours, 60-min off-hours.
- Cache empty results with 30s TTL per master template Section 10.

---

## 7. Demo narrative

### Opening (30 seconds)

"We've built a platform that lets financial advisors serve clients on
crypto ETF allocations with the same rigor and speed they serve every
other asset class. Advisors today are either ignoring crypto entirely —
which is getting harder as clients ask — or cobbling together research
from 4 different free tools and executing blind. We give them one
workspace: risk-profiled portfolios, institutional-grade backtests,
one-click execution, all compliant-by-default."

### The workflow (2 minutes)

Show dashboard → click into moderate-risk client → point to performance
panel (1Y/3Y/5Y/since-inception, benchmark, max drawdown, Hypothetical
results disclaimer per SEC Marketing Rule) → click Execute basket
(2 clicks dashboard to execution).

### The drill-down (1 minute)

Click an ETF → show expense ratio, AUM, tracking error, signal engine's
current view → drill into underlying BTC with MVRV, SOPR, funding rates.

### Positioning close (30 seconds)

**Framing A (focused):** "Deliberately focused on crypto ETFs. Best-in-class
advisor tool for this one asset class. Focus is why our signals are better
calibrated than a general-purpose tool."

**Framing B (platform):** Flip `EXTENDED_MODULES_ENABLED` → "What you see
as the ETF module is the first release of our broader platform. Same
math, same UI, extending to tokenized real-world assets and DeFi yield
strategies. Rolling out one asset class at a time for advisor confidence."

### Handling skeptical questions

- **"What about compliance?"** → SEC Marketing Rule compliant by default.
  Audit log on every action. Disclosures auto-appended. Methodology
  documented.
- **"Where's the broker integration?"** → Partnership in progress. Demo
  shows end-to-end workflow with mock execution; production 2-3 weeks out.
- **"Custom allocation logic?"** → Portfolio engine is parametric. Risk
  tiers, category ceilings, rebalance frequencies all configurable.
- **"How vs [competitor]?"** → Most competitors bolt crypto onto a
  traditional portfolio tool. We started from the crypto signal engine
  and added the advisor workflow on top.

---

## End of build package
