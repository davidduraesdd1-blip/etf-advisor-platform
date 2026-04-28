# Pending Work — ETF Advisor Platform

---

## Sprint 4 post-demo follow-ups (2026-05-01)

- [ ] **Full-position streaming.** Subscribe to Alpaca's
  `account_updates` WebSocket channel alongside the existing
  `trade_updates` subscription so the Portfolio page can render
  real-time NAV / buying-power / equity changes alongside order
  status. Module: `integrations/alpaca_streaming.py` —
  `_build_stream` adds `stream.subscribe_account_updates(...)` and
  a parallel callback registry keyed off `account_id`. UI surface:
  a "Live account" card on the Dashboard or top of Portfolio.
  Backoff and reconnect strategy is shared with the existing
  `trade_updates` loop.

- [ ] **Crypto trading stream.** Alpaca's crypto endpoint is a
  separate WebSocket: `paper-crypto-api.alpaca.markets/stream`,
  not the same as the equities `paper-api.alpaca.markets/stream`.
  When the ETF Advisor moves beyond spot-ETF wrappers (IBIT, FBTC,
  ETHA, etc.) into native crypto custody, this needs its own
  TradingStream wired in parallel — same backoff strategy, but a
  second daemon thread + second callback registry. Current Sprint
  4 module is equities-only by design.

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
- [x] F: get_etf_reference EDGAR → issuer → ETF.com → seed chain (issuer + ETF.com scrapers are post-demo stubs; seed-file marked as CACHED so FA sees tran
---

## Sprint 2026-04-24 — UI/UX full redesign (handoff from Cowork)

**Handoff doc:** `../shared-docs/CLAUDE-CODE-HANDOFF.md`
**Design system:** `../common/ui_design_system.py` — copy to `ui/design_system.py`
**Research:** `../shared-docs/design-research/2026-04-23-redesign-research.md`
**Mockups for this app** (all in `../shared-docs/design-mockups/`):
  - `advisor-etf-portfolio.html — Portfolio`
  - `advisor-etf-DASHBOARD.html — Dashboard`
  - `advisor-etf-DETAIL.html — ETF Detail`
  - `advisor-etf-METHODOLOGY.html — Methodology`

**Family:** advisor
**Accent:** #0fa68a (muted advisor-teal) — SEPARATE advisor family (not sibling)
**Priority note:** LAST in order. Demo-critical — May 1 meeting. main branch stays demo-stable until user explicitly approves merge.

### Redesign tasks — work in order, commit after each

- [x] 1. Copy `common/ui_design_system.py` → `ui/design_system.py`. Import in `app.py`. Call `inject_theme("etf-advisor-platform")` at the top of every page (after `set_page_config`, before `apply_theme`). *(Done in `redesign/ui-2026-05` commit `97efb11`.)*
- [x] 2. Replace `ui/sidebar.py` with the new left-rail design per mockup. Include: brand header, user-level selector, theme toggle, refresh button, mode indicators. Shared sidebar must render on every page (DV-1 pattern — reuse `render_sidebar()`). *(Done; sidebar redesigned in fixes-Commit-3 — grouped nav (ADVISOR / RESEARCH / ACCOUNT). Level radio + theme button **dropped** from sidebar — they were duplicates of topbar controls.)*
- [x] 3. Port the landing/home page per its mockup. First page to commit + verify end-to-end. *(Sprint 1 Commit 6, fixes Commit 1 polished primary color.)*
- [x] 4. Port each remaining page, one per commit. Match the mockup for that page in layout, spacing, component choice. *(Sprint 1 commits 2-5: Dashboard / Portfolio / ETF Detail / Methodology.)*
- [x] 5. Replace every hard-coded hex color in component code with a CSS variable reference or a `tokens`/`ACCENTS` lookup. *(Done in Sprint 1 + cleanup Commit 3 (chart line color #00d4aa → #0fa68a).)*
- [ ] 6. Verify both dark and light themes on every page. Verify mobile viewport (≤768px) on every page. *(**DEFERRED** to post-demo. Dark mode walked end-to-end; light mode + mobile not yet verified by human eyes.)*
- [x] 7. Ensure every data-consuming card has a `data_source_badge()` call per master template §10. *(Verified — Portfolio + ETF Detail composition + chart all have badges.)*
- [x] 8. Run the post-change audit per CLAUDE.md §24 after each commit — 7 criteria pass, commit message has short summary, `MEMORY.md` has full findings. *(Per-commit audit done; MEMORY.md 2026-04-26 entry covers all three sprints.)*
- [ ] 9. Run `python tests/verify_deployment.py --env prod` after every push to `redesign/ui-2026-05` branch. Walk the 20-point checklist when the branch deploys to a test URL. *(**Pending** test-deploy URL pointing at `redesign/advisor-2026-05-fixes`.)*
- [ ] 10. When all pages are done + user-approved: open a PR `redesign/advisor-2026-05` → `main`. Do NOT merge without explicit user approval. *(**Pending** user walkthrough at all 3 user levels for May 1 demo.)*

### Post-demo acceptance criteria (deferred from May 1)
*(originally "Acceptance criteria (all must be ✓ before merge to main)";
renamed 2026-04-26 audit-round-1 commit 8 — items below are not blocking
the May 1 demo per CLAUDE.md §22, they are post-demo polish.)*

- [x] Every page renders in the new design language
- [ ] Dark + light mode pass visually on every page *(dark ✓; light not yet walked)*
- [ ] Mobile viewport (≤768px) degrades gracefully on every page *(not yet walked)*
- [x] All existing unit tests pass; new tests added for new UI components *(212/212 passing as of cleanup sprint Commit 1.)*
- [ ] Deploy verifier passes 100% on `redesign/advisor-2026-05-fixes` deployed to a test URL *(deploy URL change pending)*
- [ ] Full 20-point browser checklist ✓ on test deploy
- [x] `MEMORY.md` has "Redesign complete — 2026-XX-XX" entry with per-page audit *(2026-04-26 entry added)*
- [ ] User has reviewed the live test deploy and approved the look

---

## Post-demo backlog (deferred from May 1)

Items intentionally NOT in scope for the May 1 demo. Pick up after.

- [ ] **Topbar level pills + theme button — wire to handlers.** Currently
      decorative HTML rendered alongside the page header. Same state as
      crypto-signal shipped in. Should write `st.session_state["user_level"]`
      and trigger `st.rerun()` on click, mirroring crypto-signal-app's
      `render_top_bar(on_refresh=..., on_theme=...)` pattern.
- [ ] **Light-mode end-to-end visual walk.** Toggle theme on every page,
      verify DS tokens flip correctly, no inline-styled dark colors leak
      through, contrast meets WCAG AA on every text/background pair.
- [ ] **Mobile (≤768px) end-to-end walk.** Each of the 5 pages at 390px
      and 768px viewports. Streamlit's native column-stacking should
      handle most of it, but verify exec-row, KPI strip, roster table,
      and signal hero card all degrade gracefully.
- [ ] **Collapse legacy `:root` block in `ui/theme.py`.** ~200 lines of
      `--primary` / `--card` / `--text` / `--bg` tokens still referenced
      by inline-styled HTML in `ui/components.py` and a few scattered
      page bodies. Migrate every consumer to DS tokens (`--accent` /
      `--bg-1` / `--text-primary`) then delete the legacy block.
      Explicit risk: any inline-style sweep needs visual regression
      testing, hence post-demo.
- [x] **CF S/K + MDD recalibration on altcoin holdout.**  *(2026-04-28
      hotfix on main: per-category live fit shipped + sign-convention
      bug fix in cornish_fisher_var + no-fallback policy. altcoin_spot
      empirically clamps Maillard caps (S=-1.5, K=15.0) confirming
      altcoin fat-tailedness exceeds literature midpoint defaults.
      Marcus T4 VaR_95: ~33% → 64.07% (correct conservative direction).
      See docs/math_audit_round_5_2026-04-28.md.)*
- [x] **Cold-boot perf root cause.**  *(2026-04-28 hotfix on main:
      19.5s -> 2.45s fresh / 1.47s persisted via three-state circuit
      breaker with disk persistence. Production target ≤8s ✓.)*
- [x] **AUM live wire-up to SEC EDGAR + cryptorank.io / SoSoValue.**
      *(2026-04-29 Sprint 2 on main: multi-source live chains for
      AUM (yfinance → EDGAR N-PORT → ETF.com → issuer-site), 30D Flow
      (cryptorank → SoSoValue → Farside → N-PORT-derived), and Avg Vol
      (yfinance 3M → 10D → ETF.com → 60D history). Production-snapshot
      safety net at core/etf_flow_production.json. Cron pre-warm +
      freshness indicator. See docs/etf_flow_data_chain.md.)*
- [~] **Sprint 2.6 coverage gap — 119/211 AUM.** *(2026-05-01 Sprint
      2.7 on branch `polish/sprint-2.7-playwright-cryptorank-2026-05-01`
      — branch + tag pushed, NOT merged to main.)* Sprint 2.7 closed
      the four-issuer subgoal:
      1. ✓ **2.7a — Cryptorank endpoint URL fix.** Speculative
         `v1/etfs/...` corrected to actual `v2/funds/etf`. Documented
         dead-end: free tier returns 403 ("Endpoint is not available
         in your tariff plan"). Activates automatically on tier upgrade.
      2. ✓ **2.7b — Playwright + per-fund-domain probes.**
         * Bitwise: discovered static-HTML path (per-fund domains
           `<ticker>etf.com`). 8 of 26 tickers reachable WITHOUT
           Playwright. JSON pattern `"netAssets":<float>`.
         * Franklin Templeton: Playwright works. 3 of 4 tickers
           reachable (EZBC=$491.45M, EZET=$46.65M, EZPZ=$11.69M).
         * Fidelity: DOCUMENTED DEAD-END. Playwright also fails
           with ERR_HTTP2_PROTOCOL_ERROR from datacenter IPs.
         * ETF.com: DOCUMENTED DEAD-END. Cloudflare turnstile
           defeats both static AND Playwright.
      3. _STILL PENDING_ **2.7c — issuer extractors for VanEck /
         21Shares / Hashdex / Canary / Roundhill / Defiance /
         Direxion / Calamos.** Long tail; smoke-test each first.
         Out of Sprint 2.7 scope; post-demo work.
      4. _STILL PENDING_ **2.7d — parent-CIK + per-series resolver
         for SEC** (so BITO/BITQ/GFIL etc. that file under
         ProShares Trust II et al. get covered via the XBRL chain).
         Out of Sprint 2.7 scope.

- [ ] **Fidelity AUM via residential proxies (post-demo, paid).**
      Sprint 2.7 documented Fidelity datacenter-IP block. Both
      static HTTP and Playwright fail with ERR_HTTP2_PROTOCOL_ERROR.
      ~3 universe Fidelity tickers (FBTC, FETH, etc.) stay
      em-dashed until residential-proxy infra is wired.

- [ ] **ETF.com AUM via paid scrape infra (post-demo).**
      Sprint 2.7 documented Cloudflare turnstile block. Either
      paid residential-proxy + browser-fingerprinting OR the
      official ETF.com paid API. Post-demo work.

- [ ] **Cryptorank ETF flow endpoint — paid tier upgrade.**
      Sprint 2.7 confirmed `/v2/funds/etf` exists but is gated to
      Basic/Pro/Enterprise tiers. Pricing demo-request only. The
      code already calls the correct endpoint — tier upgrade
      activates immediately, no code change required.

- [x] **Sprint 2.5 coverage gap.** *(2026-04-29 Sprint 2.5 on main:
      capture wrote 113 AUM / 124 Vol / 6 Flow with 0 errors. Sprint 2.6
      addressed via 3 issuer extractors + EDGAR facts resolver — see
      Sprint 2.6 entry above.)*
- [ ] **Per-issuer scrape extractors for issuers 7+** (currently only
      top 6 issuers have scaffold extractor entries — bespoke DOM
      parsers per issuer; VanEck / 21Shares / Hashdex / Canary /
      Roundhill / Defiance / Direxion / etc. all post-demo).
- [ ] **Replace CF with NIG / POT / GH tail model at extreme moments —
      eliminates need for feasibility clip on alt-heavy baskets.**
      *(2026-04-28 hotfix #2 landed the feasibility clip + boundary
      disclosure UI as a pragmatic interim fix. The CF polynomial at
      Maillard caps + 99% confidence extrapolates past 100% loss; the
      clip displays the long-only hard bound + footnote rather than
      the impossible polynomial value. Post-demo: replace CF with POT
      (McNeil & Frey 2000), NIG, or generalized hyperbolic — eliminates
      polynomial extrapolation at extreme tails. Multi-day refactor.
      See docs/math_audit_round_5_2026-04-28.md §5.)*
- [ ] **Investigate the 4 baseline test failures' root causes.** Cleanup
      sprint Commit 1 made them pass with `DEMO_MODE_NO_FETCH=1`, which
      is the right test-harness fix, but the underlying cause (page
      cold-boot exceeding 10s when yfinance is unreachable) is still
      worth understanding. Could be a perf optimization opportunity
      for real users hitting the live deploy on a yfinance-down day.

- [x] **Client adapter abstraction.** *(2026-04-30 Sprint 3 on main:
      pluggable ClientAdapter ABC + 5 live implementations
      (demo / csv_import / wealthbox / redtail / salesforce_fsc).
      Each CRM adapter performs real HTTP calls when keyed; falls
      back to demo when unconfigured. Settings panel surfaces
      adapter status. 25 new tests, 388/388 passing.
      See docs/client_adapter_chain.md.)*

- [ ] **Salesforce FSC OAuth refresh background job.** Sprint 3's
      Salesforce adapter assumes the operator maintains a fresh
      SALESFORCE_FSC_ACCESS_TOKEN env var (tokens expire ~2 hours).
      Production-readiness needs a background OAuth 2.0 client-
      credentials refresh process. Out of demo scope.

- [ ] **CRM-side custom fields for assigned_tier + portfolio_value.**
      Sprint 3's CRM adapters leave assigned_tier="(unassigned)" and
      total_portfolio_usd=0 because CRMs don't natively store these.
      Advisors set them via the platform's Onboarding flow post-
      import. Post-demo: define a CRM custom-field convention
      (e.g., Wealthbox custom field "Crypto Tier") so import
      brings these values through directly.

- [ ] **CRM bidirectional sync.** Sprint 3 is read-only — platform
      pulls clients from CRM but doesn't push portfolio updates,
      rebalance flags, or audit-log entries back. Bidirectional
      sync is a separate, larger sprint.
