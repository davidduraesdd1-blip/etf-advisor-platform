# ETF Advisor Platform

Advisor-facing portfolio platform for crypto ETFs, with extensible modules
for RWA and DeFi coverage. Built for financial advisors who need
institutional-grade research, risk-profiled portfolio construction, and
two-click basket execution across the crypto ETF universe.

## Status

**Private repo** — demo phase. Not yet production-ready. See `CLAUDE.md`
for governance and `docs/BUILD_PACKAGE.md` for the build plan.

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
