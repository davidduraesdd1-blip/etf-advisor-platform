"""
Methodology — the math, data, and compliance story behind the platform.

Linked from performance disclosures on the Portfolio and ETF Detail pages.
Written at a Beginner-level reading tone with an "Advanced" expander on
each section for the FA who wants the full story.
"""
from __future__ import annotations

import streamlit as st

from config import BRAND_NAME
from ui.components import card, disclosure, section_header
from ui.level_helpers import level_text
from ui.sidebar import render_sidebar
from ui.theme import apply_theme


st.set_page_config(page_title=f"Methodology — {BRAND_NAME}", layout="wide")
apply_theme()
render_sidebar()

try:
    from ui import render_top_bar as _ds_top_bar, page_header as _ds_page_header
    _ds_top_bar(breadcrumb=("Research", "Methodology"),
                user_level=st.session_state.get("user_level", "Advisor"))
    _ds_page_header(
        title="Methodology",
        subtitle=level_text(
                     advisor="Full methodology reference — linked from performance disclosures.",
                     client="How the platform constructs portfolios, measures risk, and sources data.",
                 ),
    )
except Exception:
    section_header(
        "Methodology",
        level_text(
            advisor="Full methodology reference — linked from performance disclosures.",
            client="How the platform constructs portfolios, measures risk, and sources data.",
        ),
    )


# ── 2026-04-25 redesign: port body to advisor-etf-METHODOLOGY.html ──────────
# 8-section structured doc with sticky TOC on the left + article on the right.
# Single render — methodology is reference content, not level-gated. Existing
# level_text variants for the 5 prior sections are preserved in git history;
# the deeper engineering detail (Wilder RSI tuning, Cornish-Fisher MDD
# factor, etc.) lives in docs/port_log.md and inline code comments where
# it's load-bearing.

# Inline CSS for the TOC + article — the design-system stylesheet doesn't
# style article-style long-form copy because Methodology is the only page
# with this layout in the app.
_methodology_css = """
<style>
.eap-meth-shell {
  display: grid; grid-template-columns: 220px 1fr; gap: 40px;
  align-items: start;
}
.eap-meth-toc {
  position: sticky; top: 100px; align-self: start; font-size: 13px;
}
.eap-meth-toc-title {
  font-size: 11px; color: var(--text-muted);
  text-transform: uppercase; letter-spacing: 0.08em; margin-bottom: 12px;
}
.eap-meth-toc a {
  display: block; color: var(--text-secondary); text-decoration: none;
  padding: 6px 10px; border-radius: 6px; font-size: 13px; margin-bottom: 2px;
}
.eap-meth-toc a:hover { background: var(--bg-2); color: var(--text-primary); }
.eap-meth-article h1 {
  font-family: var(--font-display); font-weight: 500;
  font-size: 32px; letter-spacing: -0.015em;
  margin: 0 0 12px; line-height: 1.2; color: var(--text-primary);
}
.eap-meth-article .sub {
  color: var(--text-secondary); font-size: 15px;
  margin-bottom: 28px; max-width: 68ch;
}
.eap-meth-article h2 {
  font-family: var(--font-display); font-weight: 500;
  font-size: 22px; letter-spacing: -0.01em;
  margin: 32px 0 12px; padding-top: 24px;
  border-top: 1px solid var(--border);
  color: var(--text-primary);
  scroll-margin-top: 100px;
}
.eap-meth-article h2:first-of-type { border-top: none; padding-top: 0; }
.eap-meth-article p {
  font-size: 14px; line-height: 1.7;
  max-width: 68ch; color: var(--text-secondary); margin: 0 0 14px;
}
.eap-meth-article p strong { color: var(--text-primary); }
.eap-meth-article ul {
  padding-left: 20px; margin: 0 0 14px;
  color: var(--text-secondary); font-size: 14px; line-height: 1.7;
}
.eap-meth-article ul li { margin-bottom: 6px; }
.eap-meth-article code {
  font-family: var(--font-mono); background: var(--bg-2);
  padding: 2px 6px; border-radius: 4px; font-size: 12.5px;
  color: var(--text-primary);
}
.eap-meth-callout {
  display: flex; gap: 14px; align-items: flex-start;
  padding: 16px 20px; margin: 20px 0; font-size: 13px;
  background: color-mix(in srgb, var(--accent) 5%, var(--bg-1));
  border: 1px solid color-mix(in srgb, var(--accent) 20%, var(--border));
  border-left: 3px solid var(--accent); border-radius: 8px;
}
.eap-meth-callout.warn {
  background: color-mix(in srgb, var(--warning) 5%, var(--bg-1));
  border-color: color-mix(in srgb, var(--warning) 20%, var(--border));
  border-left-color: var(--warning);
}
.eap-meth-callout .icon {
  width: 22px; height: 22px; border-radius: 50%;
  background: var(--accent-soft); color: var(--accent);
  display: grid; place-items: center; font-weight: 600; font-size: 13px;
  flex-shrink: 0;
}
.eap-meth-callout.warn .icon {
  background: color-mix(in srgb, var(--warning) 14%, transparent);
  color: var(--warning);
}
.eap-meth-card {
  background: var(--bg-1); border: 1px solid var(--border);
  border-radius: var(--card-radius); padding: 20px; margin: 18px 0;
}
.eap-meth-card-title {
  font-family: var(--font-display); font-weight: 500;
  font-size: 15px; margin: 0 0 10px; color: var(--text-primary);
}
.eap-meth-table {
  width: 100%; border-collapse: collapse;
  font-size: 12.5px; margin: 12px 0;
}
.eap-meth-table th {
  text-align: left; padding: 10px 12px 8px;
  border-bottom: 1px solid var(--border);
  font-size: 10.5px; color: var(--text-muted);
  text-transform: uppercase; letter-spacing: 0.07em; font-weight: 500;
}
.eap-meth-table td {
  padding: 10px 12px; border-bottom: 1px solid var(--border);
  color: var(--text-primary);
}
.eap-meth-table tr:last-child td { border-bottom: none; }
.eap-meth-table tr:nth-child(even) td { background: var(--bg-2); }
.eap-meth-table td.num { text-align: right; font-family: var(--font-mono); }
.eap-meth-table td .role {
  display: inline-block; padding: 2px 8px; border-radius: 999px;
  font-size: 10.5px; font-weight: 600;
}
.eap-meth-table td .role.primary {
  background: var(--accent-soft); color: var(--accent);
}
.eap-meth-table td .role.secondary {
  background: color-mix(in srgb, var(--info) 14%, transparent); color: var(--info);
}
.eap-meth-table td .role.tertiary {
  background: var(--bg-3); color: var(--text-muted);
}
@media (max-width: 1024px) {
  .eap-meth-shell { grid-template-columns: 1fr; gap: 24px; }
  .eap-meth-toc { position: static; display: none; }
}
</style>
"""

# ── 2026-04-25 fix: Streamlit's markdown parser converts any line with 4+
# leading spaces into a `<pre><code>` block BEFORE unsafe_allow_html=True
# kicks in. Our pretty-formatted HTML strings below have plenty of
# 4-space-indented lines (nested <section>/<article> children). Without
# normalizing the whitespace, the entire methodology page rendered as
# raw HTML markup as plain text. The helper strips line-leading whitespace
# so every line is flush-left — markdown can't mistake it for code.

def _html(raw: str) -> str:
    """Flush-left an HTML string so st.markdown's code-block rule
    (4+ leading spaces = <pre><code>) doesn't fire on indented HTML."""
    return "\n".join(line.lstrip() for line in raw.split("\n"))


st.markdown(_html(_methodology_css), unsafe_allow_html=True)

# Render the entire 2-col article in one HTML block — TOC anchors + article
# sections with id="..." attributes for the sticky links.
_article_html = """
<div class="eap-meth-shell">
  <nav class="eap-meth-toc">
    <div class="eap-meth-toc-title">On this page</div>
    <a href="#intro">Intro &amp; compliance</a>
    <a href="#construction">Portfolio construction</a>
    <a href="#signals">Signal engine</a>
    <a href="#benchmark">Benchmark</a>
    <a href="#data">Data sources</a>
    <a href="#risk">Risk metrics</a>
    <a href="#simplifications">Known simplifications</a>
    <a href="#compliance">Compliance statement</a>
  </nav>
  <article class="eap-meth-article">
    <h1>Methodology</h1>
    <div class="sub">The math, data, and compliance story behind every portfolio, signal, and performance display. Linked from every performance disclosure per SEC Marketing Rule.</div>

    <section id="intro">
      <h2>Intro &amp; compliance</h2>
      <p>This platform constructs risk-tiered crypto ETF baskets for financial advisors. Every portfolio view shows backtested performance over multiple time horizons, a blended benchmark, and max drawdown — never a single cherry-picked number. All client profiles shown in demo mode are fictional.</p>
      <div class="eap-meth-callout warn"><div class="icon">!</div><div><strong>Hypothetical results.</strong> All performance shown is hypothetical backtested or simulated. Past performance does not guarantee future results. Nothing on this platform is investment advice.</div></div>
    </section>

    <section id="construction">
      <h2>Portfolio construction</h2>
      <p>Every client is assigned one of five risk tiers — Ultra Conservative, Conservative, Moderate, Aggressive, Ultra Aggressive — each with a maximum crypto-ETF allocation ceiling applied against the <strong>total portfolio</strong>, not just the crypto sleeve.</p>
      <ul>
        <li><strong>Tier 1 — 5% ceiling.</strong> BTC spot ETFs with lowest expense ratio (IBIT, FBTC, BITB). Quarterly rebalance.</li>
        <li><strong>Tier 2 — 10% ceiling.</strong> BTC-heavy mix with ETH starting to enter. Quarterly rebalance.</li>
        <li><strong>Tier 3 — 20% ceiling.</strong> Balanced BTC/ETH, diversified across multiple issuers. Bi-monthly rebalance.</li>
        <li><strong>Tier 4 — 35% ceiling.</strong> BTC + ETH + emerging thematic ETFs as approved. Monthly rebalance.</li>
        <li><strong>Tier 5 — 50%+ ceiling.</strong> Maximum diversification across the approved universe. Bi-weekly rebalance.</li>
      </ul>
      <p>The engine (<code>core/portfolio_engine.py::build_portfolio</code>) selects up to 3 ETFs per category sorted by expense ratio, applies an issuer-tier nudge (BlackRock / Fidelity +2pp; smaller issuers neutral or −2pp), enforces a 30% per-position diversification cap, and renormalizes weights to 100%.</p>
    </section>

    <section id="signals">
      <h2>Signal engine</h2>
      <p>Each ETF gets one composite signal: <code>BUY</code>, <code>HOLD</code>, or <code>SELL</code>. Composite aggregates four layers with weights that auto-adjust based on inferred market regime:</p>
      <ul>
        <li><strong>Layer 1 — Technical.</strong> Volume, momentum, trend, volatility adapted from coin-level indicators to ETF price action. Today: Wilder RSI(14) + MACD(12,26,9) + 20-period momentum.</li>
        <li><strong>Layer 2 — Macro.</strong> Rates, DXY, VIX, credit spreads. Crypto ETFs are rate-sensitive.</li>
        <li><strong>Layer 3 — Sentiment.</strong> Fear &amp; Greed, funding rates, spot-ETF flow data (SoSoValue + cryptorank.io), news sentiment.</li>
        <li><strong>Layer 4 — On-chain.</strong> MVRV, SOPR, active addresses, exchange flows, TVL — aggregated from the underlying coins each ETF holds, weighted by holdings.</li>
      </ul>
      <p>When OHLCV history is &lt; 35 bars (newly-launched ETF), the signal falls back to a Phase-1 return-to-volatility heuristic and is clearly labeled <code>phase1_fallback</code> in the signal display.</p>
    </section>

    <section id="benchmark">
      <h2>Benchmark</h2>
      <p>Our default benchmark is an <strong>80% traditional 60/40 + 20% BTC spot sleeve</strong> blend. The crypto-ETF sleeve <em>replaces</em> 20 percentage points of the traditional allocation, not an add-on:</p>
      <div class="eap-meth-card">
        <div class="eap-meth-card-title">Benchmark composition</div>
        <table class="eap-meth-table">
          <thead><tr><th>Component</th><th>Weight</th><th>Rationale</th></tr></thead>
          <tbody>
            <tr><td>SPY</td><td class="num">48%</td><td>Equities — 60% of the 80% traditional sleeve</td></tr>
            <tr><td>AGG</td><td class="num">32%</td><td>Bonds — 40% of the 80% traditional sleeve</td></tr>
            <tr><td>IBIT</td><td class="num">20%</td><td>BTC spot — the crypto sleeve replacing traditional exposure</td></tr>
          </tbody>
        </table>
      </div>
      <p>Weights sum to 100% — they describe the full portfolio after the crypto carve-out.</p>
    </section>

    <section id="data">
      <h2>Data sources &amp; fallback chains</h2>
      <p>Every data source has a primary, secondary, and tertiary fallback. Circuit breakers stop retries on repeated failure and surface a visible state to the FA. Cache TTLs are tight where needed and loose where data moves slowly.</p>
      <table class="eap-meth-table">
        <thead><tr><th>Data</th><th>Role</th><th>Source</th><th>Cadence</th></tr></thead>
        <tbody>
          <tr><td>ETF prices (intraday)</td><td><span class="role primary">primary</span></td><td>yfinance</td><td>5 min cache (market hours)</td></tr>
          <tr><td>ETF prices (fallback)</td><td><span class="role secondary">secondary</span></td><td>Stooq (~15-min delayed)</td><td>daily</td></tr>
          <tr><td>ETF reference (holdings, AUM)</td><td><span class="role primary">primary</span></td><td>SEC EDGAR N-PORT</td><td>24h cache</td></tr>
          <tr><td>ETF flow data</td><td><span class="role primary">primary</span></td><td>cryptorank.io flow endpoints</td><td>30 min cache</td></tr>
          <tr><td>Macro rates</td><td><span class="role primary">primary</span></td><td>FRED (3-month T-bill)</td><td>daily</td></tr>
          <tr><td>Risk-free fallback</td><td><span class="role tertiary">tertiary</span></td><td>4.25% static (footnoted in UI)</td><td>—</td></tr>
        </tbody>
      </table>
      <p><code>core/data_source_state.py</code> tracks four states per category — <strong>LIVE</strong>, <strong>FALLBACK_LIVE</strong>, <strong>CACHED</strong>, <strong>STATIC</strong>. Every fetch registers; the UI reads state and surfaces it via the <code>data_source_badge</code> primitive on every data-consuming panel. The platform never silently serves stale or fabricated data.</p>
    </section>

    <section id="risk">
      <h2>Risk metrics</h2>
      <p>Every performance display includes the SEC Marketing Rule compliance set: 1Y / 3Y / 5Y / since-inception returns, benchmark comparison, max drawdown. For newer funds (e.g., spot BTC ETFs launched Jan 2024), unavailable horizons are annotated <code>N/A (&lt;3Y hist)</code> rather than silently omitted.</p>
      <p>Advanced views additionally expose <strong>Sharpe</strong> (3Y, FRED-live risk-free), <strong>Sortino</strong> (Sortino &amp; van der Meer 1991 with MAR=live_rf), <strong>Calmar</strong>, <strong>VaR 95%</strong> + <strong>VaR 99%</strong> (Cornish-Fisher with crypto-ETF-tuned skew/kurtosis), <strong>CVaR 95%</strong> + <strong>CVaR 99%</strong>, and <strong>Monte Carlo</strong> distributions (10,000 paths, block bootstrap with correlated returns, deterministic seed).</p>
      <p>Max drawdown via Magdon-Ismail-Atiya approximation; MDD factor 2.7 retuned for crypto-ETF volatility profile (vs RWA 3.0 / equity 2.3-2.5).</p>
    </section>

    <section id="simplifications">
      <h2>Known simplifications</h2>
      <div class="eap-meth-callout"><div class="icon">i</div><div>We call out every simplification rather than hiding it. Advisors reviewing this page should know exactly where the model stops.</div></div>
      <ul>
        <li><strong>Benchmark rebalancing.</strong> Static weights, no daily rebalance. Understates benchmark volatility slightly; close enough for advisor-facing display.</li>
        <li><strong>Benchmark max drawdown.</strong> Weighted average of component max drawdowns, not computed on synthetic blended equity curve. Approximation noted on every performance view.</li>
        <li><strong>Transaction costs.</strong> Backtests assume 12 bps slippage; real-world may differ per broker.</li>
        <li><strong>Tax treatment.</strong> Ignored. The platform is pre-tax; advisors apply client-specific tax policy separately.</li>
        <li><strong>Securities lending income.</strong> Not modeled. Some ETF issuers earn lending income that flows to holders; our backtest ignores this.</li>
      </ul>
    </section>

    <section id="compliance">
      <h2>Compliance statement</h2>
      <p>Every performance display on every page includes: multiple time horizons, benchmark comparison, max drawdown, a <em>Hypothetical results</em> disclaimer, and a link back to this methodology page. This satisfies the SEC Marketing Rule requirements for performance presentations to US financial advisors.</p>
      <p>All client profiles are fictional demo personas. Real client data is never stored or shown in demo mode.</p>
      <div class="eap-meth-callout warn"><div class="icon">!</div><div><strong>This platform is a research and workflow tool, not investment advice.</strong> FAs using this platform are responsible for applying their own fiduciary judgment, suitability analysis, and compliance review before any client-facing action.</div></div>
    </section>
  </article>
</div>
"""
st.markdown(_html(_article_html), unsafe_allow_html=True)
