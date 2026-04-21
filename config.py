"""
ETF Advisor Platform — configuration constants.

All runtime flags, color tokens, cache TTLs, and tier structures live here.
Nothing in this file reaches out to the network or filesystem at import time.

CLAUDE.md governance: Sections 6, 7, 8, 10, 12, 13, 22.
"""
from __future__ import annotations

import os
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────────
PROJECT_ROOT: Path = Path(__file__).resolve().parent
DATA_DIR: Path = PROJECT_ROOT / "data"
DOCS_DIR: Path = PROJECT_ROOT / "docs"

# ── Branding (CLAUDE.md §6) ───────────────────────────────────────────────────
# A full rebrand is two lines: change these constants and ship.
BRAND_NAME: str = "ETF Advisor Platform"
BRAND_LOGO_PATH: str | None = None  # set to a local path when a real logo exists

# ── Feature flags (CLAUDE.md §22, §2) ─────────────────────────────────────────
# EXTENDED_MODULES_ENABLED toggles Framing A (ETF-only) vs Framing B
# (ETF + RWA + DeFi). Default False = conservative framing.
EXTENDED_MODULES_ENABLED: bool = False

# DEMO_MODE seeds fictional clients and prebuilt portfolios so the app
# demos correctly without live internet. Toggle to False post-launch.
DEMO_MODE: bool = True

# BROKER_PROVIDER: "mock" confirms orders without hitting any API.
# Post-demo: "alpaca_paper" for sandbox, "alpaca" for live.
BROKER_PROVIDER: str = "mock"

# ── Data source routing (CLAUDE.md §10) ───────────────────────────────────────
# One-line flips to upgrade to paid tiers. Actual key values come from env.
ETF_REFERENCE_SOURCE: str = "edgar"       # "edgar" | "etfcom_api" | "morningstar"
ETF_PRICE_SOURCE: str = "yfinance"        # "yfinance" | "polygon" | "iex" | "finnhub"

# ── API keys (from environment only — never committed) ────────────────────────
ALPHA_VANTAGE_API_KEY: str | None = os.environ.get("ALPHA_VANTAGE_API_KEY")
POLYGON_API_KEY: str | None = os.environ.get("POLYGON_API_KEY")
ETFCOM_API_KEY: str | None = os.environ.get("ETFCOM_API_KEY")
FINNHUB_API_KEY: str | None = os.environ.get("FINNHUB_API_KEY")
ALPACA_API_KEY: str | None = os.environ.get("ALPACA_API_KEY")
ALPACA_API_SECRET: str | None = os.environ.get("ALPACA_API_SECRET")
ALPACA_BASE_URL: str = os.environ.get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
SENTRY_DSN: str | None = os.environ.get("SENTRY_DSN")

# ── Design tokens (CLAUDE.md §8) ──────────────────────────────────────────────
COLORS: dict[str, str] = {
    "primary": "#00d4aa",   # teal
    "success": "#22c55e",   # green (brand; used on dark backgrounds)
    "danger": "#ef4444",    # red (brand; used on dark backgrounds)
    "warning": "#f59e0b",   # amber
    "dark_bg": "#0d0e14",
    "dark_card": "#111827",
    "light_bg": "#f1f5f9",
    "light_card": "#ffffff",

    # Darker variants used specifically for badge text on tinted-white
    # backgrounds in light mode so WCAG AA contrast is met (3:1 for
    # non-text UI). The brand greens/reds above are kept for dark mode.
    "success_on_light": "#15803d",   # WCAG AA vs tinted white
    "danger_on_light":  "#b91c1c",
    "warning_on_light": "#b45309",
}

# Typography clamp() floors — never cross these minimums.
TYPE_SCALE: dict[str, str] = {
    "body": "clamp(13px, 0.9vw, 15px)",
    "label": "clamp(11px, 0.75vw, 13px)",
    "heading": "clamp(18px, 1.4vw, 24px)",
    "kpi": "clamp(22px, 1.8vw, 32px)",
}

FONTS: dict[str, str] = {
    "ui": "'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
    "data": "'JetBrains Mono', 'SF Mono', Consolas, monospace",
}

# ── Cache TTLs in seconds (CLAUDE.md §12) ─────────────────────────────────────
CACHE_TTL: dict[str, int] = {
    "client_statuses": 60,             # 1 min — near-real-time dashboard
    "etf_price_market": 300,           # 5 min during market hours
    "etf_price_offhours": 3600,        # 60 min off-hours + weekends
    "etf_holdings": 86400,             # 24 hours — holdings rarely change intraday
    "portfolio_output": 600,           # 10 min — tier+universe hash keyed
    "monte_carlo": 600,                # 10 min
    "monitoring_signals": 900,         # 15 min
    "empty_result": 30,                # 30 sec — poison-cache protection
}

# Daily scanner schedule (US Eastern, post-close).
DAILY_SCANNER_HOUR_ET: int = 16
DAILY_SCANNER_MINUTE_ET: int = 30

# ── User levels (CLAUDE.md §7) ────────────────────────────────────────────────
USER_LEVELS: tuple[str, ...] = ("Beginner", "Intermediate", "Advanced")
DEFAULT_USER_LEVEL: str = "Beginner"

# ── Risk tiers (CLAUDE.md §13) — full allocations land on Day 2 ───────────────
PORTFOLIO_TIERS: dict[str, dict] = {
    "Ultra Conservative": {
        "tier_number": 1,
        "ceiling_pct": 5,
        "rebalance": "quarterly",
        "typical_client": "retiree or explicitly risk-averse",
        "focus": "lowest-expense BTC spot ETFs (IBIT, FBTC, BITB)",
        "max_drawdown_pct": 15,
    },
    "Conservative": {
        "tier_number": 2,
        "ceiling_pct": 10,
        "rebalance": "quarterly",
        "typical_client": "near-retirement, diversification allocation",
        "focus": "BTC-heavy mix with ETH allocation starting",
        "max_drawdown_pct": 25,
    },
    "Moderate": {
        "tier_number": 3,
        "ceiling_pct": 20,
        "rebalance": "bi-monthly",
        "typical_client": "mid-career, moderate risk tolerance",
        "focus": "balanced BTC/ETH across multiple issuers",
        "max_drawdown_pct": 40,
    },
    "Aggressive": {
        "tier_number": 4,
        "ceiling_pct": 35,
        "rebalance": "monthly",
        "typical_client": "younger, higher risk tolerance, longer horizon",
        "focus": "BTC + ETH + emerging thematic ETFs",
        "max_drawdown_pct": 55,
    },
    "Ultra Aggressive": {
        "tier_number": 5,
        "ceiling_pct": 50,
        "rebalance": "bi-weekly",
        "typical_client": "high-conviction crypto allocator",
        "focus": "max diversification across all approved crypto ETFs",
        "max_drawdown_pct": 70,
    },
}

# ── Starter ETF universe (CLAUDE.md §13) — scanner expands this on Day 2 ──────
ETF_UNIVERSE_SEED: list[dict[str, str]] = [
    # Spot Bitcoin ETFs
    {"ticker": "IBIT", "issuer": "BlackRock",  "category": "btc_spot",   "name": "iShares Bitcoin Trust"},
    {"ticker": "FBTC", "issuer": "Fidelity",   "category": "btc_spot",   "name": "Fidelity Wise Origin Bitcoin Fund"},
    {"ticker": "BITB", "issuer": "Bitwise",    "category": "btc_spot",   "name": "Bitwise Bitcoin ETF"},
    {"ticker": "ARKB", "issuer": "ARK/21Shares","category": "btc_spot",  "name": "ARK 21Shares Bitcoin ETF"},
    {"ticker": "BTCO", "issuer": "Invesco",    "category": "btc_spot",   "name": "Invesco Galaxy Bitcoin ETF"},
    {"ticker": "EZBC", "issuer": "Franklin",   "category": "btc_spot",   "name": "Franklin Bitcoin ETF"},
    {"ticker": "BRRR", "issuer": "Valkyrie",   "category": "btc_spot",   "name": "Valkyrie Bitcoin Fund"},
    {"ticker": "HODL", "issuer": "VanEck",     "category": "btc_spot",   "name": "VanEck Bitcoin Trust"},
    {"ticker": "BTC",  "issuer": "Grayscale",  "category": "btc_spot",   "name": "Grayscale Bitcoin Mini Trust"},
    {"ticker": "GBTC", "issuer": "Grayscale",  "category": "btc_spot",   "name": "Grayscale Bitcoin Trust"},
    {"ticker": "DEFI", "issuer": "Hashdex",    "category": "btc_futures","name": "Hashdex Bitcoin Futures ETF"},
    # Spot Ethereum ETFs
    {"ticker": "ETHA", "issuer": "BlackRock",  "category": "eth_spot",   "name": "iShares Ethereum Trust"},
    {"ticker": "FETH", "issuer": "Fidelity",   "category": "eth_spot",   "name": "Fidelity Ethereum Fund"},
    {"ticker": "ETHE", "issuer": "Grayscale",  "category": "eth_spot",   "name": "Grayscale Ethereum Trust"},
    {"ticker": "ETHW", "issuer": "Bitwise",    "category": "eth_spot",   "name": "Bitwise Ethereum ETF"},
    {"ticker": "CETH", "issuer": "21Shares",   "category": "eth_spot",   "name": "21Shares Core Ethereum ETF"},
    {"ticker": "QETH", "issuer": "Invesco",    "category": "eth_spot",   "name": "Invesco Galaxy Ethereum ETF"},
    {"ticker": "EZET", "issuer": "Franklin",   "category": "eth_spot",   "name": "Franklin Ethereum ETF"},
    {"ticker": "ETH",  "issuer": "Grayscale",  "category": "eth_spot",   "name": "Grayscale Ethereum Mini Trust"},
]

# ── Benchmark for backtest comparisons (CLAUDE.md §22 item 5) ─────────────────
# Blended 60/40 equity/bond index + BTC spot sleeve.
BENCHMARK_DEFAULT: dict[str, float] = {
    "SPY": 0.48,    # 60% of 80% = equity
    "AGG": 0.32,    # 40% of 80% = bonds
    "IBIT": 0.20,   # 20% BTC spot sleeve
}

# ── Rate limits ──────────────────────────────────────────────────────────────
EDGAR_REQS_PER_SEC: int = 10              # SEC hard cap
YFINANCE_TICKERS_PER_CALL: int = 50       # batch ceiling to be safe

# ── SEC EDGAR identifier (Day-2 Mod 2 / Day-3 Q2 env override) ───────────────
# SEC requires an identifiable User-Agent with contact email for all
# programmatic access. daily_scanner() raises RuntimeError if this is still
# the placeholder. .env override takes precedence over repo-committed default.
EDGAR_CONTACT_EMAIL: str = os.environ.get(
    "EDGAR_CONTACT_EMAIL",
    "REPLACE_BEFORE_DEPLOY@example.com",
)

# ── Monte Carlo memory ceiling (Day-2 Mod 4 / Risk 4) ────────────────────────
# Compute with full sample count for accurate math; retain far fewer paths for
# UI rendering so @st.cache_data doesn't blow the 1GB Streamlit Cloud ceiling.
MONTE_CARLO_PATHS_COMPUTE: int = 10_000
MONTE_CARLO_PATHS_RETAIN: int = 250
MONTE_CARLO_MEMORY_CEILING_MB: int = 100

# ── yfinance circuit breaker (Day-2 Mod 3) ───────────────────────────────────
YF_CIRCUIT_BREAKER_WINDOW_SEC: int = 60
YF_CIRCUIT_BREAKER_THRESHOLD: int = 3
