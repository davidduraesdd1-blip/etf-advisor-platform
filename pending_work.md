# Pending Work — ETF Advisor Platform

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

## Day 3 — Thursday 2026-04-23 (advisor UI)

- [ ] Dashboard page: mock client list, sortable/filterable, status column, rebalance flags
- [ ] Portfolio page: allocation chart (pie + bar), performance panel (1Y/3Y/5Y/since-inception), "Execute basket" CTA with confirmation modal
- [ ] ETF detail page: signal badge, composition breakdown, backtest card, underlying-coin indicator panel
- [ ] Settings page: broker routing dropdown, monitoring preferences, auto-execute toggle, `EXTENDED_MODULES_ENABLED` dev toggle
- [ ] Beginner/Intermediate/Advanced scaling across every page (plain-English → condensed → raw metrics)
- [ ] Dark + light theme audit on every view
- [ ] Commit + push + tag `backup-pre-day4-2026-04-23`
- [ ] Audit pass + report

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
