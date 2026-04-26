# ETF Advisor Platform

Advisor-facing portfolio platform for crypto ETFs, with extensible modules
for RWA and DeFi coverage. Built for financial advisors who need
institutional-grade research, risk-profiled portfolio construction, and
two-click basket execution across the crypto ETF universe.

## Status

**Private repo** — demo phase. Not yet production-ready. See `CLAUDE.md`
for governance and `docs/BUILD_PACKAGE.md` for the build plan.

**Active branch:** `redesign/advisor-2026-05-fixes` — full advisor-family
redesign + mockup-parity fixes pending Cowork's walkthrough before merge
to `main`. Demo target: May 1 2026. See `MEMORY.md` for the per-sprint
commit log and `pending_work.md` for the deferred backlog.

## Design system

The platform uses the **advisor family** of the 2026-05 redesign — a
warmer charcoal dark palette + paper-white light palette, with a serif
display font (Source Serif 4) for headings and a muted teal accent
(`#0fa68a`).

- **Mockups:** `../shared-docs/design-mockups/advisor-etf-*.html` —
  Dashboard, Portfolio, ETF Detail, Methodology.
- **Tokens:** `ui/design_system.py` — single source of truth for colors,
  fonts, layout primitives. Per-app accent in `ACCENTS["etf-advisor-platform"]`.
- **Streamlit theme:** `.streamlit/config.toml` pins `primaryColor` to
  the advisor teal so every native widget (radios, toggles, sidebar
  primary buttons) matches the design.
- **5 pages ported** to the redesign: Home (`app.py`), Dashboard,
  Portfolio, ETF Detail, Methodology. Settings is operator-only, kept
  on legacy primitives by design.

## Stack

- Python 3.11
- Streamlit (multi-page)
- pandas / numpy / scipy
- yfinance (primary price data)
- plotly (charts)

## Local setup

```bash
git clone https://github.com/davidduraesdd1-blip/etf-advisor-platform.git
cd etf-advisor-platform
python -m venv venv
source venv/bin/activate         # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env             # edit with your keys (optional for demo)
streamlit run app.py
```

App opens at `http://localhost:8501`.

## Configuration

All runtime flags live in `config.py`:

- `EXTENDED_MODULES_ENABLED` — `False` (default): ETF-only. `True`: adds RWA + DeFi preview tabs.
- `DEMO_MODE` — `True` (default): seeds fictional clients; app works offline.
- `BROKER_PROVIDER` — `"mock"` (default): confirms orders without hitting a real broker.

## Docs

- [`CLAUDE.md`](CLAUDE.md) — master agreement (governance, standards, protocols).
- [`docs/BUILD_PACKAGE.md`](docs/BUILD_PACKAGE.md) — full build plan Mon-Fri.
- [`docs/architecture.md`](docs/architecture.md) — system architecture overview.

## Foundation repos (reference only — not imported at runtime)

- [`crypto-signal-app`](https://github.com/davidduraesdd1-blip/crypto-signal-app) — signal engine patterns.
- [`rwa-infinity-model`](https://github.com/davidduraesdd1-blip/rwa-infinity-model) — portfolio construction engine.
- [`flare-defi-model`](https://github.com/davidduraesdd1-blip/flare-defi-model) — architectural template.

## License

Proprietary. All rights reserved.
