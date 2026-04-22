# Streamlit Cloud Deploy — ETF Advisor Platform

Private-app deployment guide for the demo. First-time only; subsequent
deploys are push-to-main.

## Pre-flight (5 minutes)

Run this locally before connecting the repo, to surface any dependency
issues where you can debug them:

```bash
cd /path/to/etf-advisor-platform
python -m venv fresh_env
source fresh_env/bin/activate           # Windows: fresh_env\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

If the local fresh-env boot works cleanly, Cloud will too. If it fails,
add the missing package to `requirements.txt` and commit before
connecting the repo.

## Connect the repo to Streamlit Cloud

1. Sign in at `https://share.streamlit.io` with the GitHub account that
   owns `davidduraesdd1-blip/etf-advisor-platform`.
2. Click **"New app"** → **"From existing repo"**.
3. Fill in:
   - **Repository:** `davidduraesdd1-blip/etf-advisor-platform`
   - **Branch:** `main`
   - **Main file path:** `app.py`
   - **App URL:** pick a memorable subdomain (recommend
     `etf-advisor-demo` so the URL is predictable).
4. **Advanced settings → Python version:** 3.11 (matches `runtime.txt`).
5. Click **"Deploy!"**. The first cold boot takes ~90 seconds while
   Cloud installs `requirements.txt` + `packages.txt`.
6. If the build fails, read the build log tail — 90% of first-deploy
   failures are one missing dep. Add it, commit, Cloud auto-redeploys.

## Secrets configuration

In the Streamlit Cloud app settings (gear icon → "Secrets"), paste a
TOML block. **Do not commit `.streamlit/secrets.toml` to the repo** —
only the `.example` file is committed; the real file is in Cloud only.

Minimum viable secrets (required for EDGAR scanner to run at all):

```toml
EDGAR_CONTACT_EMAIL = "ops@your-domain.example"
```

Full secrets block (enable every live data path):

```toml
EDGAR_CONTACT_EMAIL = "ops@your-domain.example"
# Alpha Vantage is NOT wired into the active price chain. Its free tier
# is 25 req/day — insufficient even for one user across the 19-ETF
# universe, so it was removed as a false fallback. Scaffold retained:
# set ALPHA_VANTAGE_API_KEY here AND re-enable the _fetch_alphavantage
# call in integrations/data_feeds.py::_fetch_single_ticker (currently
# documented-out with a comment) to reactivate on a paid tier.
# ALPHA_VANTAGE_API_KEY = "paid_tier_key_required"
POLYGON_API_KEY = "your_polygon_key"
FINNHUB_API_KEY = "your_finnhub_key"
SENTRY_DSN = "https://...@sentry.io/..."
```

Not needed for demo:
- `ALPACA_API_KEY`, `ALPACA_API_SECRET`, `ALPACA_BASE_URL` — unused while
  `BROKER_PROVIDER="mock"` (day-of-demo setting).

## How secrets map to code

Streamlit Cloud's `st.secrets` also auto-populates `os.environ` at app
start. Our `config.py` reads via `os.environ.get(...)` so **the same
code path works locally (`.env`) and on Cloud (secrets TOML)**. No
if/else branches anywhere.

## First-boot verification checklist

After the first successful boot on Cloud, walk through the 4 pages:

- **Dashboard** — 3 clients render with DEMO watermark.
- **Portfolio** — pick Moderate tier. Allocation pie renders. Forward-
  projection Monte Carlo renders. `data_source_badge` is absent (LIVE)
  when yfinance works; amber badge when Stooq fallback or cache.
- **ETF Detail** — pick IBIT. Live historical chart renders. Signal
  shows `technical_composite`. Composition card shows EDGAR N-PORT
  data (may take a few seconds on first fetch; subsequent loads are
  7-day cached).
- **Settings** — Scanner health shows whether EDGAR config is clean.
  Audit log panel shows seeded demo entries. DSS snapshot shows the
  state of each category.

## If something breaks mid-demo

1. Cloud apps have a **"Reboot app"** button — nukes the process and
   restarts. Clears `@st.cache_data` and `@st.cache_resource` state but
   does NOT wipe the secrets or the repo.
2. EDGAR returning 429 → `integrations/edgar.py` token bucket + 3-try
   exponential backoff. User sees an amber CACHED badge and the app
   keeps running.
3. yfinance breaking → circuit breaker auto-flips to Stooq. User sees
   amber FALLBACK_LIVE badge.

## Post-demo teardown (don't forget)

If the demo is one-off:
- Streamlit Cloud → App settings → Delete app.
- Revoke any throwaway API keys you generated.
- Keep the repo private unless your post-demo discussion decides otherwise.
