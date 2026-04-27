# Memory Index

Session continuity log. Newest entries on top. See master-template §16.

---

## 2026-04-29 — Sprint 2: ETF Detail everything-live data (no-fallback)

Cowork directive: "everything real and live, no hardcoded fallback
values." Sprint 2 lands on `main` per the post-Sprint-1 freeze lift.
Replaces the hardcoded `_AUM_REFERENCE_STUB_USD` + `_ETF_REFERENCE_STUB`
blocks with multi-source live chains for AUM / 30D net flow / avg daily
volume across all 211 universe tickers.

### What landed (5 commits)

  1. **integrations/etf_flow_data.py** — multi-source live chains.
     `get_etf_aum`, `get_etf_30d_net_flow`, `get_etf_avg_daily_volume`
     each return `(value, source_name)` tuples, never raise. Per
     CLAUDE.md §10 fallback-chain pattern; key-gated cryptorank step
     skipped without raising when CRYPTORANK_API_KEY unset. Runtime
     cache `data/etf_flow_cache.json` with 24h TTL + no-poison-cache
     for None values. 20 new tests.
  2. **core/etf_flow_production.json** (NEW, COMMITTED) — production
     safety-net snapshot. Bootstrap content carries the 14 major spot
     ETFs from the prior reference values (source-tagged
     "reference (bootstrap)" so the UI is honest about provenance).
     Remaining 197 tickers have null entries; live chain or nightly
     cron pre-warm fills them at render time.
     **scripts/refresh_etf_flow_production.py** — patient capture
     script (5-attempt backoff, 30s cooldown, resume-from-progress)
     for operator-driven refreshes.
  3. **pages/03_ETF_Detail.py** — AUM / 30D Flow / Avg Vol tiles
     wired through the live multi-source chain. Each tile shows a
     small "via <source>" badge below the value. 30D Flow tile uses
     semantic green/red based on sign. Em-dash footnote appears
     ONLY when all three tiles return None. Hardcoded
     `_ETF_REFERENCE_STUB` block REMOVED.
  4. **core/scheduler.py** — `prewarm_etf_flow_cache(universe)` walks
     all 211 tickers and pre-populates the cache; writes per-source
     distribution to `portfolio_snapshot.json::flow_prewarm`. The
     cron's `recalculate_all_portfolios` calls it automatically.
     ETF Detail page header reads the summary and surfaces a
     freshness indicator: "Data refreshed: 23m ago · 187/211 tickers
     live · 24/211 from snapshot". 5 new tests.
  5. **docs/etf_flow_data_chain.md** (NEW) — full chain spec, source
     registry, refresh cadence, operator runbook. MEMORY entry +
     pending_work mark for "AUM live wire-up".

### Test count

  327 (Sprint-1 baseline) → 332 commit 4 → final after this commit.
  + 20 in test_etf_flow_data.py
  +  5 in test_scheduler_flow_warming.py

### Tag

`audit-round-4-etf-detail-live-2026-04-29` on main.

---

## 2026-04-28 — Hotfix #2: CF feasibility clip + boundary disclosure

Cowork flagged a math-display problem in the post-no-fallback numbers:
Marcus Avery T4 VaR_99 = 171.70% and CVaR_95 = 132.79% — mathematically
impossible for a long-only basket (can't lose more than principal).
Cornish-Fisher polynomial extrapolates past the feasible region when
(S, K) hit the Maillard caps and confidence is high. Pragmatic fix
ships per Cowork's call: clip at the asset-class hard bound (100%) +
boundary disclosure UI; better tail models (NIG / POT / GH) are
post-demo.

### What landed (3 commits)

  1. **fix(cf):** feasibility clip in `cornish_fisher_var` / `cvar`.
     `CFRiskResult(value, cf_boundary_reached)` NamedTuple return type;
     `_clip_to_loss_bound` helper; `compute_portfolio_metrics` propagates
     4 boundary flags + `any_cf_boundary_reached` logical-OR. Existing
     CF VaR tests updated for `.value` access.
  2. **feat(ui):** `risk_metrics_panel(metrics, sleeve_usd)` helper
     in `ui/components.py`. Tiles display "≤ -$X / -100% / model
     boundary" when boundary reached. Calm-tone footnote with
     methodology link appears when any tile hit the bound. Wired into
     Portfolio page inside `st.expander("Advanced risk metrics —
     Advisor mode")`. New `<section id="cf-boundary">` in
     pages/98_Methodology.py.
  3. **docs:** Round-5 doc updated with §5 Boundary handling
     (rationale, post-demo NIG/POT plan), MEMORY entry, pending_work
     post-demo line for tail-model replacement.

### Marcus Avery T4/T5 risk panel rendering

Both tiers now show:
- VaR_95: 64.07% (T4) / 62.38% (T5) — under boundary
- VaR_99: **≤ -$81,200 / -100% / model boundary**
- CVaR_95: **≤ -$81,200 / -100% / model boundary**
- CVaR_99: **≤ -$81,200 / -100% / model boundary**

Footnote + methodology link visible. The 171.70% display is gone.

### Test count: 297 → 307 (+10)

  + 8 in TestFeasibilityClip
  + 2 in TestRiskMetricsPanelUI

### Tag

`audit-round-4-cf-clip-2026-04-28` on main.

---

## 2026-04-28 — Production hotfix: CF live params + sign correction + no-fallback policy

Cowork lifted the freeze on `main` for this targeted hotfix.
Directive: **"everything real and live the entire time, no
hardcoded fallback defaults."** Branch
`hotfix/cf-live-no-fallback-2026-04-28` cherry-picked the 3 Sprint 1
commits from the polish branch + 2 new commits, merged to main.

### What landed (5 commits)

  1. **Cherry-pick:** CF calibration infrastructure (`core/cf_calibration.py`,
     `tests/test_cf_calibration.py`).
  2. **Cherry-pick:** Sign-convention bug fix in `cornish_fisher_var`
     + per-category CF wire-up. The pre-existing bug (using +z_g
     right-tail instead of -z_α left-tail) had been silently under-
     estimating VaR ~50-60% on negatively-skewed crypto returns.
     CVaR was already correct.
  3. **Cherry-pick:** Cold-boot perf — persisted three-state circuit
     breaker (yfinance / stooq / unavailable) with 5-min TTL.
     Cold-boot 19.5s → 2.45s fresh / 1.47s persisted.
  4. **NEW: Patient live fit + production-config + no-fallback policy.**
     - `core/cf_calibration.py` strengthened with per-ticker
       exponential backoff (1, 2, 4, 8, 16s), inter-category 30s
       cooldown, resume-from-progress.
     - `core/cf_params_production.json` (NEW, COMMITTED, force-added
       past .gitignore) carries the live-fitted (btc_spot, eth_spot,
       altcoin_spot) + 7 nearest-neighbor overrides for the categories
       that fell back during the patient fit (yfinance rate-limited).
       Each override has a documented `rationale`.
     - `_get_cf_params(category)` precedence: cache → production-config
       → **RuntimeError**. No third level. No silent fallback.
     - `_CF_DEFAULT_SKEW` / `_CF_DEFAULT_KURT` constants REMOVED. Only
       place those numbers appear is the `deprecated_constants` block
       in `cf_params_production.json` for audit trail.
     - `cornish_fisher_var` / `cornish_fisher_cvar` now require explicit
       (S, K). Calling with None raises.
  5. **NEW: Documentation + tag.** `docs/math_audit_round_5_2026-04-28.md`
     captures methodology, per-category result table, sign-fix
     numerical example (Marcus Avery T4: VaR_95 ~33% → 64.07%),
     operational guidance for refreshing the snapshot, risk summary.

### Production-config snapshot

| Category | S | K | Source |
|---|---:|---:|---|
| btc_spot | -0.058 | 2.570 | live (5y, 11 funds) |
| eth_spot | -0.264 | 2.141 | live (5y, 9 funds) |
| altcoin_spot | -1.500 | 15.000 | live (5y, 16 funds, **MAILLARD CAPS HIT**) |
| btc_futures | -0.058 | 2.570 | nearest_neighbor → btc_spot |
| eth_futures | -0.264 | 2.141 | nearest_neighbor → eth_spot |
| leveraged | -0.058 | 2.570 | nearest_neighbor → btc_spot |
| income_covered_call | -0.058 | 2.570 | nearest_neighbor → btc_spot |
| multi_asset | -0.140 | 2.398 | blended 60% btc_spot + 40% eth_spot |
| thematic_equity | -0.140 | 2.398 | nearest_neighbor → multi_asset |
| defined_outcome | -0.140 | 2.398 | nearest_neighbor → multi_asset |

### Marcus Avery T4 VaR shift (sign-fix headline)

| Stage | VaR_95 | VaR_99 | CVaR_95 |
|---|---:|---:|---:|
| Pre-sign-fix (main HEAD `3214a54`) | ~33% | ~52% | ~52% |
| Post-sign-fix only (deprecated constants) | 65.33% | 228.16% | 170.00% |
| **Post no-fallback (production-config)** | **64.07%** | **171.70%** | **132.79%** |

The sign-fix correction is the headline (~50-60% more conservative
VaR on typical crypto baskets). The slight drop from sign-fix-only
to no-fallback is the per-category fit refinement (BTC less
fat-tailed in 2024-2026 ETF window than literature midpoints).

### Test count

297 / 297 passing (was 262 pre-hotfix, +35 new tests).

### Tag

`audit-round-4-cf-live-2026-04-28` on `main` HEAD.

---

## 2026-04-26 — Audit-round-2 (overnight verification pass)

Re-audit of the 8-commit + 7-bonus round-1 work and a deep math /
universe-fairness pass per user override ("dig as deeply as necessary
to ensure that everything including all the math functions and calls
the portfolio determinations and weights are as accurate as possible
make sure that you have included the daily routine of searching deeply
for and adding when deemed correct the new etfs to the full list and
making sure that you are fully considering all the etfs including those
that are alt coins and leveraged etfs as well").

### Round 2 audits run + results

  1. **build_portfolio sanity** — every tier sums to 100.00% weight,
     Sharpe is finite, MDD ≤ tier ceiling for all 5 tiers.
  2. **Forward-return per altcoin** — SOL/XRP/LTC/DOGE/ADA/AVAX/HBAR/
     DOT/LINK each route through `_altcoin_cagr_or_none` first. When
     yfinance has data → per-coin CAGR with no haircut. When not →
     BTC × 0.70 fallback with EXPLICIT basis string flagging the
     fallback path. Confirmed in
     `integrations/data_feeds.py::get_forward_return_estimate`.
  3. **Leveraged + income wrappers on alts** — `_underlying_cagr()`
     resolves SOLT/XRPT through the per-coin path. Leveraged uses
     1.10× (vol-decay-adjusted, not naive 2.0×). Income covered-call
     uses 0.55× (call-cap haircut).
  4. **AUM tiebreaker actually fires** — verified BTC spot category
     has 5 funds at 25 bps (IBIT/FBTC/BTCO/BRRR/BTCW). Within the
     tie, IBIT ($62B) ranks ahead of FBTC ($20B), then issuer
     diversity. Fee + AUM + diversity all functional in the sort key.
  5. **Universe coverage** — 82 ETFs in live universe across 10
     categories; 16 altcoin spots (5 SOL, 4 XRP, 2 LTC, 2 DOGE, 1
     each of ADA/AVAX/HBAR), 12 leveraged, 13 income covered-call.
  6. **Composite signal across categories** — every category renders
     a BUY/HOLD/SELL with consistent threshold logic. Phase-1 fallback
     path active when no live history available; technical_composite
     path takes over when history present. Both paths labeled in
     output `source` field.
  7. **Daily scanner GitHub Action** — `.github/workflows/daily_scanner.yml`
     fires at 17:00 UTC daily, runs `daily_scanner(days_back=3)`,
     pushes any data/ changes. Both `data/scanner_health.json` AND
     `data/etf_review_queue.json` are auto-committed back to repo
     when there are new findings. Manual override via Settings page
     "Run scanner now" button.
  8. **Tier allocation matrix** — Ultra Conservative excludes
     altcoin entirely (correct for retiree IPS); Conservative +
     Moderate stay BTC/ETH-only; Aggressive adds 15% altcoin;
     Ultra Aggressive adds 20% altcoin + 5% leveraged + 10% thematic
     equity. Compliance filter (default ON) strips the 5% leveraged
     sleeve and redistributes proportionally.
  9. **Weight cap respected** — every holding ≤ MAX_SINGLE_POSITION_PCT
     (30%) at every tier. Largest holdings: Tier 1 = 20% on BTC.
  10. **261/261 tests passing**. Verifier 9/9 on prod.

### Round 2 verdict

No code changes required. The round-1 + bonus scope already addressed
every fairness concern. New `docs/math_audit_round_2_2026-04-26.md`
captures the verification chain. Tag `audit-round-1-2026-04-26` is
the final demo-ready state.

---

## 2026-04-26 — Audit-round-1 + bonus scope shipped (overnight session)

**Tag `audit-round-1-2026-04-26`** shipped on `main` after the post-bucket
demo-ready landing. Cowork's 9 P0 + 4 P1 audit findings + the user's
"don't defer anything" expansion all closed in this pass.

### Audit-round-1 commits (8) — all green

  1. **fix(pages):** import-time hardening on every page file. Each of
     the 5 pages (`app.py` + `pages/01..03,99`) wraps its body in
     `def main()` with an `if __name__ == "__main__": main()` guard.
     Importing via `importlib.import_module` no longer crashes on
     `st.set_page_config` outside a Streamlit context. New helper
     `ui/page_runtime.py::safe_streamlit_import` carries the
     ModuleNotFoundError-retry pattern for hot-reload teardown.
  2. **fix(state):** `core/data_source_state.py` `@dataclass` guard.
     `Optional[X]` → `X | None` (Python 3.10+ syntax) drops the
     `from typing import Optional` import dependency that races
     `@dataclass` evaluation under partial sys.modules teardown.
     50× re-import loop test confirms.
  3. **feat(detail):** ETF Detail SEC Marketing Rule compliance —
     `performance_summary_table` (1Y/3Y/5Y/since-inception + benchmark
     + max drawdown) wired below KPIs; canonical
     `hypothetical_results_disclosure()` helper renders the disclaimer;
     `safe_page_link` to Methodology. AppTest covers every render.
  4. **feat(banner):** Verbatim CLAUDE.md §22 item 4 banner via new
     `extended_modules_banner()` helper. Wired onto Dashboard's
     cross-asset preview (RWA + DeFi sleeves) so all extended-module
     surfaces speak the same compliance language.
  5. **test:** 3 new test files — `test_import_hot_reload.py` (10×
     re-import loop on every page + 50× reload of data_source_state),
     `test_etf_review_queue.py` (full Bucket 3 coverage — enrich,
     add_pending dedup, approve, reject, additions sidecar, garbage-
     JSON robustness), `test_data_feeds.py` extended with
     `TestYfinance429FallsBackToStooq`. Total 261/261 passing.
  6. **fix(tone):** removed 🚩 + reworded "red flag" on premium-to-NAV
     callout; removed 🛡 from fiduciary-filter caption; standardized
     all hypothetical-results wording through `hypothetical_results_disclosure()`.
  7. **fix(color):** `config.COLORS["primary"]` imports the canonical
     advisor accent from `ui/design_system.py::ACCENTS`. Plotly hex
     codes in `pages/02_Portfolio.py` + `pages/03_ETF_Detail.py`
     replaced by `_ACCENT_HEX` / `_ACCENT_RGBA_FAN` /
     `_ACCENT_RGBA_MEDIAN` derived from the design-system token.
     Avatar gradients on `pages/01_Dashboard.py` extracted to
     `config.AVATAR_PALETTE`.
  8. **docs:** MEMORY.md prepended with this entry; pending_work.md
     "Acceptance criteria" → "Post-demo acceptance criteria (deferred
     from May 1)" + new "Post-demo backlog" section; CLAUDE.md
     "Friday-deadline" → "May 1 hard demo deadline" (lines 8 + 302
     + 325); README.md tag link.

### Bonus scope (user override — "don't defer anything")

  - **AUM tiebreaker** (`core/portfolio_engine.py::_select_etfs_for_category`):
    expense_ratio asc, then AUM desc (larger first), then issuer
    diversity, then ticker. AUM resolves through 24-hr-memo'd
    `_get_aum_usd` (yfinance Ticker.info["totalAssets"] live → hardcoded
    `_AUM_REFERENCE_STUB_USD` for major spots → None). DEMO_MODE_NO_FETCH
    short-circuits the live path so AppTest renders stay deterministic.
  - **Real broker wiring**: `integrations/broker_alpaca_paper.py` ships
    `submit_basket_via(provider, …)` provider-router consumed by
    `pages/02_Portfolio.py` Execute Basket modal. Routes
    `mock|alpaca_paper|alpaca` with graceful fallback when alpaca-py
    isn't installed or keys missing — fallback recorded in
    `response["broker"] = "alpaca_paper_fallback_to_mock"` for audit.
    Settings page surfaces credential state next to the provider
    selectbox.
  - **AUM auto-fetch**: same module above (`_get_aum_usd`).
  - **Legacy `:root` CSS collapse** (`ui/theme.py`): legacy
    `--primary/--bg/--card/--text/--muted` now alias the design-system
    tokens (`--accent`, `--bg-0/1`, `--text-primary/muted`). Single
    source of truth.
  - **Mobile media queries + ARIA**: `@media (max-width: 768px)` block
    in `ui/theme.py` collapses 4-up KPI strips to 2-up, tightens card
    padding, scales hero typography. ARIA / focus-visible outlines
    added; `.sr-only` helper class shipped.
  - **Component dedup**: `ui/design_system.py` exports
    `kpi_tile_html / signal_badge_html / data_source_badge_html /
    compliance_callout_html` aliases so callers can disambiguate the
    HTML-string-returning helpers from `ui/components.py`'s Streamlit-
    direct equivalents.

### Math fairness re-verified (audit-round-2 reading)

  - Per-altcoin CAGR fix from Bucket 2 (2026-04-26 morning) confirmed
    in place in `integrations/data_feeds.py::get_forward_return_estimate`
    `category == "altcoin_spot"` branch. Falls through to BTC × 0.70
    only when yfinance has no per-coin history; basis string explicit.
  - Cornish-Fisher S/K previously recalibrated to crypto midpoints
    (S=−0.7 / K=8.0) per Maillard 2012 + Shahzad et al. 2022. MDD
    factor f is now Sharpe-dependent (`np.interp` over [-0.5..1.5] →
    [3.2..1.9]) per Magdon-Ismail 2004 Table 1. Both apply uniformly
    across all tiers including alt-heavy Tier 4/5.
  - Issuer-tier nudge audit reaffirmed clean — Tier C list is purely
    structural (legacy high-fee + futures-based + 40-Act swaps), no
    alt-coin bias.

### Test count + pace

  Pre-round-1: 228 passing.
  Post-round-1: 261 passing (+33). Suite runs in 25s.

### Tag

  `audit-round-1-2026-04-26` on main + remote.

---

## 2026-04-26 — Demo-ready (advisor/client taxonomy + mockup parity)

**Cascade complete.** Tag `demo-ready-2026-04-26` shipped on
`main` at `5574537`. Live deploy https://etf-advisor.streamlit.app/
verified 9/9 by `tests/verify_deployment.py --env prod`.

### What landed in a single-day cascade

Four sequential branches, **15 source-changing commits + 1 merge commit**,
all fast-forwarded onto `main`:

  1. `redesign/advisor-2026-05` (6 commits) — initial mockup port:
     CSS audit, Dashboard / Portfolio / ETF Detail / Methodology / Home
     page bodies rewritten to match `../shared-docs/design-mockups/
     advisor-etf-*.html`.

  2. `redesign/advisor-2026-05-fixes` (6 commits) — mockup-parity
     follow-ups after Cowork's first walkthrough: Streamlit primary
     color → advisor teal `#0fa68a`, methodology HTML rendering bug
     fixed, sidebar grouped nav (no level radio / theme dup / refresh
     dup), Portfolio tier pills (5 numbered) + KPI dedup + subtitle,
     ETF Detail hero + KPIs + signal callout, Dashboard inline-Open
     links per row.

  3. `chore/pre-merge-cleanup-2026-04-26` (5 commits) — pre-merge
     cleanup: `DEMO_MODE_NO_FETCH=1` short-circuit + 30s AppTest
     timeout (208/212 → 212/212), session_state autouse reset in
     conftest, ETF Detail composition side-by-side via st.columns
     ([2,1]) + DV-2 perf table to its own full-width card,
     MEMORY.md + pending_work.md + README updates.

  4. `feat/advisor-client-taxonomy-2026-04-26` (3 commits) —
     **taxonomy collapse** per Cowork directive: drop the 3-level
     Beginner / Intermediate / Advanced tree; replace with 2-mode
     Advisor (default — full data, jargon, raw indicator names, for
     the FA working alone) / Client (plain English, simpler charts,
     prominent hypothetical-results disclaimers, for screen-share with
     a client). Same data both modes, presentation only. Topbar pills
     swapped 3 → 2 (still decorative pending wiring post-demo).
     Hand-rolled paren-aware scanner rewrote 24 `level_text()` call
     sites across 5 pages without breaking multi-line strings,
     f-strings, or comments. `is_advisor()` / `is_client()` replace
     `is_advanced()` / `is_beginner()` / `is_intermediate()`.

  + 1 merge commit (`5574537`) bringing main's daily chore commits
    (scanner + analytics — data-only, 5 commits) into the cascade so
    the final advisor-2026-05 → main step was a clean fast-forward.

### Test status

  - **212/212 passing** on `main` after cascade.
  - 4 baseline failures from before the sprint (1 sidebar-persistence
    test-order leak + 3 page-apptest timeouts) — all fixed by cleanup
    sprint Commit 1 (`DEMO_MODE_NO_FETCH=1`) + Commit 2 (autouse
    session_state reset).
  - Full suite ~85s.

### Compliance status

  - **DV-1** (level/mode persists across pages): ✅ preserved through
    the 3-level → 2-mode collapse. Test rewritten to assert "Client"
    persistence (was "Advanced").
  - **DV-2** (perf table 8-column compliance set): ✅ preserved on
    Portfolio + ETF Detail. ETF Detail perf table now lives in its
    own full-width card below the columns row.
  - **SEC Marketing Rule** disclosures: ✅ Hypothetical-results
    callout on every performance display; Methodology link from each.

### Prod deploy verifier — 2026-04-26

```
── deploy verify: prod @ https://etf-advisor.streamlit.app/ ──
  ✓   1.64s  base URL reachable (<= 60s)
  ✓   1.59s  landing page — no Python error signatures
  ✓   1.71s  landing page — expected strings (3)
  ✓   1.61s  page /Dashboard
  ✓   1.62s  page /Portfolio
  ✓   1.59s  page /ETF_Detail
  ✓   1.59s  page /Methodology
  ✓   1.55s  page /Settings
  ✓   1.63s  health endpoint /_stcore/health
── 9/9 checks passed ──
```

### Resume point

Demo-ready. May 1 demo flow:
  Home → Dashboard (Marcus Avery, drift-flagged) → Portfolio (Tier 3,
  toggle to 4 and back) → Execute Basket → ETF Detail → Methodology.
  Walked at Advisor mode then flipped to Client mode for the simpler
  copy verification.

Post-demo backlog (per `pending_work.md`):
  - Topbar pill wiring (currently decorative)
  - Light-mode end-to-end walk
  - Mobile (≤768px) walk
  - Legacy `:root` block collapse in `theme.py`
  - Full doc sweep updating `pending_work.md` + `README.md` to reflect
    the new taxonomy (this MEMORY entry covers it for now).

---

## 2026-04-26 — Advisor-family redesign + mockup-parity fixes + cleanup

**Context:** Three back-to-back sprints porting the advisor-family
design system mockups onto every page of the ETF advisor platform,
then closing every visible mockup-parity gap, then a pre-merge
cleanup pass to green the test suite and stabilize for the May 1 demo.

### Sprint 1 — Initial port (`redesign/advisor-2026-05`, 6 commits)

Mockups in `../shared-docs/design-mockups/advisor-etf-*.html`:
  - `advisor-etf-DASHBOARD.html` — KPI strip + client roster + activity + compliance
  - `advisor-etf-portfolio.html` — tier pills + holdings & perf + ready-to-execute
  - `advisor-etf-DETAIL.html`    — hero + perf chart + composition + perf table
  - `advisor-etf-METHODOLOGY.html` — 8 sections w/ sticky TOC

Commits 1-6:
  1. CSS audit — annotated all `!important` rules across `ui/theme.py`
     + `ui/overrides.py` + `ui/design_system.py`; deleted 1 duplicate
     (theme.py sidebar-bg conflicting with overrides.py).
  2. Dashboard body → KPI strip · roster table · activity · compliance · callout.
  3. Portfolio body → 4-up KPI strip · exec-row · rebalance card · callout
     (preserved DV-2 perf table, allocation chart, MC fan, Execute Basket modal).
  4. ETF Detail body → hero card · signal row · callout
     (preserved DV-2 perf table, composition table, MC, signal components).
  5. Methodology body → 8-section structured doc with sticky TOC, serif h1/h2.
  6. Home body refresh (.ds-card primitives) + Settings verified.

### Sprint 2 — Mockup-parity follow-ups (`redesign/advisor-2026-05-fixes`, 6 commits)

Cowork walkthrough flagged Streamlit-default red on every primary
button/radio/toggle, methodology HTML rendering as raw text, sidebar
duplicates of topbar controls, Portfolio radio (not pills) + double
KPI strip + technical subtitle, ETF Detail hero showing dashes for
24h/1Y change + wrong KPIs, Dashboard giant-button stack instead of
inline-Open links.

Commits 1-6:
  1. `.streamlit/config.toml` — primaryColor=#0fa68a (advisor teal),
     backgroundColor=#0c0d12, font=sans serif, client.toolbarMode=minimal.
     Also bumped `.gitignore` to track config.toml (was excluded).
  2. Methodology — fixed HTML-rendering bug; all 8 sections render with
     proper typography.
  3. Sidebar — grouped nav (ADVISOR / RESEARCH / ACCOUNT), removed
     experience-level radio + Light-mode button (duplicates of topbar
     controls), renamed auto-generated "app" entry to "Home".
  4. Portfolio — replaced Streamlit radio with 5 numbered tier pills,
     deduped the second 5-KPI strip, rewrote technical subtitle for FA
     audience.
  5. ETF Detail — hero shows price + 24h % + 1Y % (was dashes), KPI swap
     to Expense / AUM / 30D Flows / Avg Vol, signal callout expanded.
  6. Dashboard — inline "Open →" link per row, drift-flagged client
     gets warning-amber color (no longer Streamlit red).

### Sprint 3 — Pre-merge cleanup (`chore/pre-merge-cleanup-2026-04-26`, 5 commits)

Audit found 4 baseline test failures predating the redesign work
(208/212). Cleanup sprint to green CI before final merge.

Commits 1-5:
  1. `tests/test_smoke.py` — `os.environ["DEMO_MODE_NO_FETCH"] = "1"` at
     module top + `default_timeout=30` on parametrized AppTest. Added
     short-circuit in `integrations/data_feeds.py::get_etf_prices` and
     `get_last_close` so universe loader bypasses the ~80-ticker
     yfinance loop in tests. **Suite: 208/212 → 212/212.**
  2. `tests/conftest.py` — autouse fixture that drops every key from
     `st.session_state` between tests. Defensive guard for the
     sidebar-persistence test-order leak; prevents future regressions.
  3. ETF Detail composition side-by-side — chart 2/3 / composition 1/3
     in `st.columns([2, 1])`; DV-2 perf summary table moved to its
     own full-width card below the columns row.
  4. (this entry) — MEMORY.md update.
  5. (next) — pending_work.md reconciliation + README refresh.

### Compliance status

  - **DV-1** (level selector persists across pages): ✅ preserved through redesign.
  - **DV-2** (perf table 8-column compliance set): ✅ preserved through redesign.
  - **SEC Marketing Rule** disclosures: ✅ on every performance display via
    Hypothetical-results callout.

### Files touched (high-level inventory)

  - `app.py` — Home body refresh
  - `pages/01_Dashboard.py` — KPI strip · roster · inline Open links · activity · compliance
  - `pages/02_Portfolio.py` — tier pills · 4-up KPI strip · exec-row · callout
  - `pages/03_ETF_Detail.py` — hero card · signal row · side-by-side chart+composition · perf table
  - `pages/98_Methodology.py` — 8-section reference doc with sticky TOC
  - `pages/99_Settings.py` — DS chrome verified, body unchanged (operator page)
  - `ui/sidebar.py` — grouped nav (ADVISOR / RESEARCH / ACCOUNT), no level/theme dups
  - `ui/design_system.py` — token registry · audit annotations
  - `ui/ds_components.py` — sidebar brand block
  - `ui/overrides.py` — Streamlit widget overrides (advisor-family palette)
  - `ui/theme.py` — legacy compat layer (annotated for post-demo collapse)
  - `.streamlit/config.toml` — Streamlit theme config (advisor teal primary)
  - `.gitignore` — track config.toml
  - `tests/test_smoke.py`, `tests/conftest.py` — DEMO_MODE_NO_FETCH + session reset

### Test status — 2026-04-26

  - **212/212 passing** (was 208/212 across all three sprints).
  - 4 baseline failures fixed: 1 sidebar-persistence test-order leak +
    3 page-apptest timeouts.
  - Full suite runs ~85s.

### Branch graph

```
main (d127275)
 └─ redesign/ui-2026-05 (656f0ee)
     │  advisor-family design system + topbar/page_header wiring
     └─ redesign/advisor-2026-05 (6eb1096)        ← Sprint 1: 6 commits
         └─ redesign/advisor-2026-05-fixes (e968000) ← Sprint 2: 6 commits
             └─ chore/pre-merge-cleanup-2026-04-26   ← Sprint 3: 5 commits (this PR)
```

### Resume point

PR `chore/pre-merge-cleanup-2026-04-26` → `redesign/advisor-2026-05-fixes`
pending Cowork's walkthrough + sign-off. After that the merge cascade
runs:
  `chore/pre-merge-cleanup-2026-04-26` → `redesign/advisor-2026-05-fixes`
  `redesign/advisor-2026-05-fixes`     → `redesign/advisor-2026-05`
  `redesign/advisor-2026-05`           → `main`

Final user walkthrough at Beginner / Intermediate / Advanced levels
required before the May 1 demo.

---

## 2026-04-23 — Baseline deployment verification (§25 Part A + B)

**Context:** First pass of §25 against live deploy at
https://etf-advisor.streamlit.app/.

### Part A — automated smoke test

`python tests/verify_deployment.py --env prod` → **9/9 checks passed**
- base URL reachable (1.57s, HTTP 200)
- no Python error signatures in landing
- expected shell markers present
- /Dashboard, /Portfolio, /ETF_Detail, /Methodology, /Settings all 200
- health endpoint /_stcore/health 200

### Part B — manual 20-point walkthrough (user-walked in browser)

- Universal 1-7, 9-20: all ✓
- **Item 8 — Level selector persistence: ⚠ BROKEN.** Selector resets on
  page navigation. Tracked as DV-1 in `pending_work.md`.
- **Item C1 — Performance display time horizons: ⚠ INCOMPLETE.** Not all
  of 1Y/3Y/5Y/since-inception shown on every performance view. Tracked
  as DV-2 in `pending_work.md`.
- Items C2-C8: all ✓ (including C7 drawdown calm messaging — earlier
  code-grep conclusion that C7 was missing was WRONG; user confirmed
  it's implemented, grep just missed the wording variant)
- X1 ✓ (`EXTENDED_MODULES_ENABLED=False` default)
- X2 ✓ (preview banner present — my earlier grep-based claim that it
  was missing was WRONG; user confirmed it renders correctly)
- Data feeds D1-D3: all ✓

### Summary

**Status: HEALTHY deploy, 2 feature bugs.** No deploy-level blockers.
Two real bugs found by user's browser walk:
- DV-1 (item 8): level selector doesn't persist across pages.
- DV-2 (item C1): performance displays missing required time horizons.

Both tracked at top of `pending_work.md`. Ship before Friday demo per §22.

### Lesson for future verifications

Code grep is a starting point, not an authority. User's browser
walkthrough is the ground truth for feature presence. When grep and
user-walk disagree, user-walk wins — the wording variant, the
conditional-load, or the runtime-computed string can hide what grep
can't see.

### Resume point

Next session: work DV-2 (time horizons — touches multiple views, needs
a shared helper). DV-1 resolved below.

---

## 2026-04-23 — DV-1 resolved (level selector persistence)

**Root cause:** `_render_sidebar()` was private to `app.py` and called
only from `app.py`. Streamlit's multipage convention runs the chosen
page script when the user navigates, NOT `app.py` — so the sidebar
(and its level selector) never rendered on pages other than the
landing view. `st.session_state["user_level"]` was fine; the value
was persisting correctly in state, but there was no widget rendered
on other pages to reflect it.

**Fix:**
1. Extracted sidebar into `ui/sidebar.py` as public `render_sidebar()`
   with an explicit `key="user_level_radio"` on the radio (guards
   against per-page widget-hash drift).
2. `app.py` imports `render_sidebar` and calls it from `main()`.
3. All 5 pages under `pages/` now `from ui.sidebar import render_sidebar`
   and call it immediately after `apply_theme()`.
4. Added `tests/test_sidebar_persistence.py` — uses Streamlit's
   `AppTest` runner to verify (a) `user_level` is initialized on first
   render, (b) a selection survives across re-renders, (c) a static
   check that every page under `pages/` imports and calls
   `render_sidebar()`. This static guard prevents the regression on
   any future page addition.

**Audit (§4 seven criteria):**
- Correctness: ✓ simulated + covered by new test
- Tests: ✓ 4 new assertions, static guard included
- Optimization: ✓ no new expensive calls; sidebar runs once per page
- Efficiency: ✓ no duplicate state writes
- Accuracy: ✓ session_state behavior per Streamlit docs
- Speed: ✓ sidebar render is ~milliseconds
- UI/UX: ✓ brand header, level selector, theme toggle, refresh button
  now visible on every page

**Files touched:**
- New: `ui/sidebar.py`, `tests/test_sidebar_persistence.py`
- Modified: `app.py`, `pages/01_Dashboard.py`, `pages/02_Portfolio.py`,
  `pages/03_ETF_Detail.py`, `pages/98_Methodology.py`,
  `pages/99_Settings.py`

**Next:** User tests locally, confirms selector persists across pages,
then this commits + pushes + redeploys + re-walks the 20-point check.

---

## 2026-04-23 — DV-2 resolved (performance display compliance)

**Observed:** Portfolio page Historical tab showed 1Y / 3Y / 5Y returns
only, with "None" for newer funds. Missing since-inception, benchmark,
and max-drawdown per CLAUDE.md §22 item 5. ETF Detail page had similar
gap — 4 KPI tiles for 1Y/3Y/5Y/Data points, no since-inception or
max drawdown.

**Fix:**
1. Added `performance_summary_table()` helper in `ui/components.py`
   (plus `_ps_simple_return_pct`, `_ps_cagr_pct`, `_ps_max_drawdown_pct`,
   `_ps_fmt_pct`, `_ps_fmt_dd`, `_ps_row`, `_ps_blended_benchmark_row`).
   Returns a pandas DataFrame with columns: ticker · source · inception ·
   1Y % · 3Y % · 5Y % · since-inception % · max drawdown %.
2. Fallback display: `"N/A (<1Y hist)"` / `"N/A (<3Y hist)"` /
   `"N/A (<5Y hist)"` instead of bare `None` when a fund is too young.
   Addresses the specific UX complaint (the bunch of "None" cells you
   saw on newer spot BTC/ETH ETFs).
3. Benchmark row: blended `BENCHMARK_DEFAULT` (SPY 48 / AGG 32 /
   IBIT 20), static-weight, no daily rebalancing. Labeled
   `Benchmark (80% traditional 60/40 + 20% BTC spot sleeve)`.
   Simplification documented on the Methodology page.
4. Updated `pages/02_Portfolio.py` (Historical tab) to use the helper
   (replaces the ad-hoc loop).
5. Updated `pages/03_ETF_Detail.py` (Historical returns card) to use
   the helper (replaces the 4 KPI tiles).
6. Added `tests/test_performance_summary.py` — 10 tests covering
   scalar helpers (max drawdown, simple return, CAGR), table
   integration (6Y / 18mo / empty), benchmark row presence/absence,
   and a drawdown-fixture verification.

**Audit (§4 seven criteria):**
- Correctness: ✓ tests cover positive and negative paths
- Tests: ✓ 10 new cases
- Optimization: ✓ benchmark prices fetched once per page (not per-row)
- Efficiency: ✓ reuses existing get_etf_prices cache
- Accuracy: ✓ CAGR matches known fixture (~100% for 100→200 over 1Y);
  max drawdown matches fixture (−75% for 200→50 peak-to-trough)
- Speed: ✓ same number of API calls as before (+1 benchmark fetch)
- UI/UX: ✓ compliance columns added; placeholders informative not blank

**Files touched:**
- New: `tests/test_performance_summary.py`
- Modified: `ui/components.py` (added ~180 lines),
  `pages/02_Portfolio.py` (Historical tab), `pages/03_ETF_Detail.py`
  (Historical returns card).

**Known limitations (documented for Methodology):**
- Blended benchmark uses static weights — doesn't account for
  rebalancing drift. Close enough for advisor-facing display; exact
  model in Methodology.
- Benchmark max drawdown is the weighted average of component max
  drawdowns, not computed on the synthetic blended equity curve.
  Approximation; tradeoff called out on Methodology page.

**Next:** User tests DV-2 locally on Portfolio + ETF Detail pages,
confirms the new columns render and "N/A (<1Y hist)" appears where
appropriate. Then commit + push + redeploy + walk the updated checklist.
