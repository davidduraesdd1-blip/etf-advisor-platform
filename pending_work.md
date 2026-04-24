# Pending Work — ETF Advisor Platform

---

## Deployment Verification Findings — 2026-04-23

Baseline pass of CLAUDE.md §25 (Deployment Verification Protocol).
User walked the 20-point checklist manually against live deploy.
Two real bugs found. Both are ⚠ non-blockers for the deploy itself
(nothing crashes), but both should ship before the Friday demo.

*Earlier code-grep conclusions about C7 (drawdown calm messaging) and*
*X2 (extended-modules preview banner) were WRONG — user confirmed both*
*are implemented. Grep missed them because of wording variants or*
*runtime-loaded strings.*

- [x] **DV-1. Item 8 — Level selector does not persist across pages.** (FIXED 2026-04-23, see MEMORY.md)
  CLAUDE.md §7 requires: "Level persists in session state."
  Universal 20-point checklist item 8: "Level selector persists across
  page navigation." User walked and found this broken.
  Expected: pick Advanced on Dashboard → navigate to Portfolio → still
  Advanced in sidebar. Observed: resets to default between pages.
  Likely cause: `st.session_state["user_level"]` is set inside the
  sidebar function but not read before initialization on each page
  load. Streamlit's multipage convention re-runs the page script on
  navigation, but session_state should survive.
  Deliverables:
    - Trace `user_level` init logic in `app.py` and every page under
      `pages/`. Confirm each page reads from `st.session_state` BEFORE
      writing a default.
    - If the sidebar component is called per-page (expected), verify
      it's idempotent re: session_state writes — only set default if
      the key is missing.
    - Unit test: simulate page-to-page navigation with Streamlit's
      `AppTest` runner; assert `user_level` value preserved.
    - Audit against §4 per §24 on commit.

- [x] **DV-2. Item C1 — Performance displays missing time horizons.** (FIXED 2026-04-23, see MEMORY.md)
  CLAUDE.md §22 item 5 + §8: "Backtest performance displays ALWAYS
  include multiple time horizons (1Y, 3Y, 5Y, since-inception)."
  User walked and found not all four time horizons appear on every
  performance display.
  Deliverables:
    - Inventory every performance display in the app — likely
      `pages/02_Portfolio.py`, `pages/03_ETF_Detail.py`,
      `98_Methodology.py`, and any backtest result views.
    - Confirm which time horizons each shows today; identify which
      are missing per display.
    - Build a single `performance_summary()` helper in
      `ui/components.py` that takes a return series and renders all
      four horizons (1Y, 3Y, 5Y, since-inception) + benchmark + max
      drawdown + hypothetical disclaimer in one block.
    - Replace every ad-hoc performance-display block with calls to
      the helper. No display can render returns without going through it.
    - Unit test: helper produces all four horizons from a sample
      5+-year return series; raises if series too short to compute
      one (don't silently omit — annotate "insufficient history").
    - Audit against §4 per §24 on commit.

---

## Day 1 — Tuesday 2026-04-21 ✅ COMPLETE

- [x] Infrastructure files (.gitignore, .gitleaks.toml, .pre-commit-config.yaml, Dockerfile, docker-compose.yml, requirements.txt, packages.txt, runtime.txt, .env.example, .python-version)
- [x] README.md expanded
- [x] docs/BUILD_PACKAGE.md, docs/architecture.md
- [x] Directory structure (pages/, core/, integrations/, ui/, data/, tests/, docs/)
- [x] config.py (flags, colors, cache TTLs, tiers, ETF seed)
- [x] app.py (multipage router, sidebar, home view)
- [x] Placeholder pages (01_Dashboard, 02_Portfolio, 03_ETF_Detail, 99_Settings)
- [x] ui/theme.py + ui/components.py
- [x] tests/test_smoke.py (parse + AppTest runner)
- [x] MEMORY.md + pending_work.md
- [x] First commit + push + tag `backup-pre-day2-2026-04-21`
- [x] Audit pass + report

## Day 2 — Wednesday 2026-04-22 (portfolio engine + ETF data)

- [ ] `core/risk_tiers.py` — tier config constants + allocation-by-category matrix
- [ ] `core/portfolio_engine.py` — adapted from `rwa-infinity-model/portfolio.py`:
  - [ ] `build_portfolio(tier_name, universe)` → holdings + weights
  - [ ] `compute_portfolio_metrics(weights, returns)` → Sharpe/Sortino/Calmar/VaR/CVaR
  - [ ] `run_monte_carlo(weights, returns, n=10000)` → distribution
  - [ ] `cornish_fisher_var(weights, returns, confidence=0.95)`
- [ ] `core/etf_universe.py`:
  - [ ] `load_universe()` → seed + scanner-added ETFs
  - [ ] `daily_scanner()` — EDGAR RSS query for N-1A / 497 / S-1 with crypto keywords
  - [ ] Per-ETF reference data struct (holdings, AUM, expense ratio, inception)
- [ ] `integrations/data_feeds.py`:
  - [ ] `get_etf_prices(tickers)` — yfinance primary, Alpha Vantage / Stooq fallback
  - [ ] `get_etf_reference(ticker)` — EDGAR primary, issuer / ETF.com fallback
  - [ ] Token-bucket rate limiter for EDGAR (10 req/sec cap)
  - [ ] 30s poison-cache TTL for empty results (CLAUDE.md §10)
- [ ] `core/signal_adapter.py` — per-ETF composite BUY/HOLD/SELL
- [ ] Unit tests: `tests/test_portfolio_engine.py`, `tests/test_etf_universe.py`, `tests/test_data_feeds.py`
- [ ] Commit + push + tag `backup-pre-day3-2026-04-22`
- [ ] Audit pass + report

## Day 3 — Wednesday 2026-04-22 ✅ COMPLETE

Morning block (Phase 2 + scanner health + data_source_state):
- [x] config.py EDGAR env override + .env.example
- [x] portfolio_engine Phase 2 — full pairwise correlation matrix
- [x] Issuer-tier nudge (+2 / 0 / -2 pp) replacing Phase-1 institutional-backing idea
- [x] Chain-maturity discount evaluated and dropped (ETFs not chain-scoped)
- [x] Phase-1 ETH correlation guard removed (call site + function)
- [x] core/etf_universe.py scanner health persistence (atomic write, 5-attempt retry)
- [x] core/data_source_state.py — 4-state tracker (LIVE / FALLBACK_LIVE / CACHED / STATIC)
- [x] data_feeds.py register_fetch_attempt wiring on every fetch

UI block:
- [x] ui/components.py data_source_badge() + tier_pill_selector()
- [x] ui/theme.py badge + banner + footnote CSS (dark + light)
- [x] ui/level_helpers.py level_text() + is_beginner/intermediate/advanced
- [x] core/demo_clients.py — 3 fictional personas explicitly labeled DEMO
- [x] pages/01_Dashboard.py — client roster table + open-portfolio nav
- [x] pages/02_Portfolio.py — tier pills + allocation chart + perf panel + MC fan + Execute Basket modal
- [x] pages/03_ETF_Detail.py — signal badge + KPIs + composition + historical returns
- [x] pages/99_Settings.py — broker routing + auto-execute toggles + scanner health + DSS snapshot
- [x] integrations/broker_mock.py estimated_slippage_bps field + Alpaca Pro TODO

Tests:
- [x] tests/test_portfolio_engine.py — Phase 2 pairwise + issuer-tier nudge + guard removal
- [x] tests/test_etf_universe.py — scanner health write/read/stale threshold (4 new tests)
- [x] tests/test_data_source_state.py — full 4-state cascade + recovery + snapshot
- [x] tests/test_theme_contrast.py — WCAG AA for token palette + 3 badge states in both themes
- [x] Determinism canary re-confirmed (seed=42 still bit-stable after Phase 2 math changes)

Close:
- [x] Commit + push + tag `backup-pre-day4-2026-04-23`
- [x] Audit report in commit message (findings + fixes + performance + security)
- [x] docs/port_log.md updated with Phase 2 entries

Deferred from Day 3 (tracked below in Day 4 block):
- Chain-maturity discount — evaluated and DROPPED as decision (recorded in port_log.md)
- Live historical prices are LIVE (not synthetic) per design directive — when fallback chain exhausts, page shows transparency message rather than fake data

## Day 4 — Thursday 2026-04-23 ✅ COMPLETE

Morning block (A-F, blocking before deploy):
- [x] A: Live yfinance w/ 24hr cache + module-level memoization in data_feeds
- [x] B: Signal adapter upgraded — RSI(14) Wilder / MACD(12,26,9) / 20-day momentum composite
- [x] C: Cornish-Fisher + CVaR + MDD retune for crypto ETFs (S=-0.25, K=2.5, CVaR 1.35/1.42, MDD factor 2.7)
- [x] D: Execute Basket modal wires get_last_close per ETF; Confirm disabled when all live prices missing
- [x] E: SEC EDGAR N-PORT parser (IBIT / ETHA / FBTC / FETH). Shared 10 req/sec token bucket in integrations/edgar.py. 7-day disk cache.
- [x] F: get_etf_reference EDGAR → issuer → ETF.com → seed chain (issuer + ETF.com scrapers are post-demo stubs; seed-file marked as CACHED so FA sees transparency)

Afternoon block (G-J, polish + demo prep):
- [x] G: Demo client profiles refined with coherent narratives + situation_today + seeded audit log
- [x] H: pages/98_Methodology.py populated with 5 real sections (construction, backtest, signal, risk, transparency)
- [x] I: Audit log panel on Settings page. 200-entry ring buffer, atomic writes, Windows retry.
- [x] J: Empty / loading / error state polish sweep across all 4 pages. Methodology link on Portfolio + ETF Detail disclosures.

Cloud deploy (K):
- [x] Streamlit Cloud deploy guide in docs/streamlit_cloud_deploy.md
- [x] .streamlit/secrets.toml.example committed
- [ ] Actual Cloud app creation + secrets population — DAVID'S ACTION (can't automate from Claude Code)

Audit + tag (L-M):
- [x] Full suite green after CF retune (property-based tests survived by design)
- [x] Audit report in commit message
- [x] Commit + push + tag `backup-demo-ready-2026-04-24`

Tests added Day 4:
- tests/test_signal_adapter.py — RSI / EMA / MACD / momentum / composite (≈20 tests)
- tests/test_edgar_nport.py — XML fixture parse + unsupported-ticker path
- tests/test_audit_log.py — append / ring-buffer / atomic-write / seed
- tests/test_edgar.py — runtime guard / user-agent / token-bucket block

## Post-demo — parking lot (Day 5+)

- [ ] Issuer-site and ETF.com reference scrapers (currently marked post-demo placeholder)
- [ ] N-PORT parser extension beyond IBIT/ETHA/FBTC/FETH to every seed ticker
- [ ] Alpaca paper-trading integration (BROKER_PROVIDER="alpaca_paper")
- [ ] Live proper 3-year calibration fit for Cornish-Fisher S/K (replace 2026-04 crypto-ETF retune placeholders)
- [ ] Signal adapter Layer 2/3/4 additions (macro, sentiment, on-chain) on top of the Day-4 technical composite
- [ ] Multi-user auth + persistent client state (Supabase or similar)
- [ ] Partner codebase merge + integration plan
- [ ] Real brand identity + logo (update BRAND_NAME + BRAND_LOGO_PATH constants)
- [ ] RWA + DeFi preview tabs wired to rwa-infinity-model + flare-defi-model patterns
- [ ] Manual WCAG visual audit (complement to the token-palette contrast tests)
- [ ] Monte Carlo runtime memory ceiling assertion (currently only defined, not enforced)
- [ ] EDGAR search endpoint integration test in CI (weekly) to catch format changes

## Day 4 — Friday 2026-04-24 (polish + demo)

- [ ] 3 demo client profiles with coherent narrative arcs (conservative retiree, mid-career moderate, high-conviction allocator)
- [ ] Polished empty / loading / error states on every page (CLAUDE.md §8)
- [ ] Performance disclaimers on every backtest
- [ ] Audit log surface (minimal "Recent actions" panel)
- [ ] `pages/98_Methodology.py` with placeholder copy
- [ ] RWA + DeFi preview tabs when `EXTENDED_MODULES_ENABLED=True`
- [ ] Streamlit Cloud private-app deploy
- [ ] Full audit pass per CLAUDE.md §4
- [ ] Commit + push + tag `backup-demo-ready-2026-04-24`

## Post-demo (parking lot)

- [ ] Real Alpaca broker integration (paper → live)
- [ ] Multi-user auth + persistent state (Supabase or similar)
- [ ] Partner codebase merge
- [ ] Production deploy with monitoring
- [ ] Real brand identity + logo
