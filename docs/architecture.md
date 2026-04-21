# Architecture Overview

## Layers

```
┌───────────────────────────────────────────────────────────────┐
│  UI LAYER  (Streamlit)                                        │
│    app.py  →  pages/*.py  →  ui/theme.py + ui/components.py   │
└───────────────────────────────────────────────────────────────┘
                           │
┌───────────────────────────────────────────────────────────────┐
│  CORE LAYER  (pure Python, testable)                          │
│    core/portfolio_engine.py   ← 5-tier construction, MC, VaR  │
│    core/etf_universe.py       ← ETF list + daily scanner      │
│    core/risk_tiers.py         ← tier config constants         │
│    core/signal_adapter.py     ← per-ETF composite BUY/HOLD/SELL│
└───────────────────────────────────────────────────────────────┘
                           │
┌───────────────────────────────────────────────────────────────┐
│  INTEGRATIONS LAYER  (external systems)                       │
│    integrations/data_feeds.py ← yfinance / EDGAR / issuer     │
│    integrations/broker_mock.py← stubbed execution             │
└───────────────────────────────────────────────────────────────┘
```

## Data flow

1. `data_feeds.py` fetches ETF prices + reference data (cached per Section 12).
2. `etf_universe.py` maintains the active ETF list and refreshes daily via EDGAR scanner.
3. `portfolio_engine.py` takes `(risk_tier, universe_snapshot)` → returns holdings, weights, metrics.
4. `signal_adapter.py` computes per-ETF BUY/HOLD/SELL from underlying-coin signals.
5. `pages/*.py` compose UI from these outputs. `ui/components.py` renders cards/badges/tables.

## Feature flags

- `EXTENDED_MODULES_ENABLED` — adds RWA + DeFi preview tabs. Default `False`.
- `DEMO_MODE` — seeds fictional clients, allows offline demo. Default `True`.
- `BROKER_PROVIDER` — `"mock"` (default) | `"alpaca_paper"` | `"alpaca"`.

## Feature-flag sequencing

Demo (Friday):     `EXTENDED=False, DEMO=True,  BROKER="mock"`
Post-demo sandbox: `EXTENDED=<decided>, DEMO=False, BROKER="alpaca_paper"`
Production:        `EXTENDED=<decided>, DEMO=False, BROKER="alpaca"`

## Foundation repos (reference only)

This repo does NOT import from the three foundation repos at runtime per
CLAUDE.md Section 11. Code patterns are copied; dependencies are not.

- `crypto-signal-app` → signal engine patterns (composite_signal, cycle_indicators).
- `rwa-infinity-model` → portfolio construction patterns (portfolio.py reference).
- `flare-defi-model` → directory structure template.
