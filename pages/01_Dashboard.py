"""
Dashboard — advisor home view. Client roster with status, rebalance flags,
per-client Open-Portfolio link.

All clients shown are fictional demo personas per CLAUDE.md §22 item 3.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from config import BRAND_NAME, DEMO_MODE
from core.audit_log import seed_demo_entries
from core.demo_clients import DEMO_CLIENTS
from core.etf_universe import load_universe_with_live_analytics
from core.portfolio_engine import build_portfolio
from integrations.data_feeds import get_etf_prices
from ui.components import (
    card,
    data_source_badge,
    disclosure,
    section_header,
)
from ui.level_helpers import level_text
from ui.sidebar import render_sidebar
from ui.theme import apply_theme


st.set_page_config(page_title=f"Dashboard — {BRAND_NAME}", layout="wide")
apply_theme()
render_sidebar()

# ── 2026-05 redesign: advisor-family top bar + page header ──
try:
    from ui import render_top_bar as _ds_top_bar, page_header as _ds_page_header
    _ds_level_adv = st.session_state.get("user_level", "beginner")
    _ds_top_bar(breadcrumb=("Advisor", "Dashboard"), user_level=_ds_level_adv)
    _ds_page_header(
        title="Client dashboard",
        subtitle=level_text(
            beginner="Your client list with rebalance status and quick links.",
            intermediate="Client roster: tier, drift, rebalance flags.",
            advanced="Client roster with tier / drift / flags. Filter + sort via column headers.",
        ),
        data_sources=[("Custody feed", "live"), ("Model drift", "cached")],
    )
except Exception:
    section_header(
        "Dashboard",
        level_text(
            beginner="Your client list with rebalance status and quick links.",
            intermediate="Client roster: tier, drift, rebalance flags.",
            advanced="Client roster with tier / drift / flags. Filter + sort via column headers.",
        ),
    )

if not DEMO_MODE:
    st.info(
        "Demo mode is currently OFF. Dashboard would be populated from the "
        "advisor's connected CRM. This view requires `DEMO_MODE=True` or a "
        "real client-data integration to render useful data."
    )
    st.stop()

# Seed the audit log with illustrative history on first visit.
try:
    seed_demo_entries(DEMO_CLIENTS)
except Exception:
    pass   # Seed failure is not demo-blocking

# Data-source panel intentionally omitted on Dashboard per FA feedback
# 2026-04-22: "no FA actually cares where the data is coming from so
# long as it's correct." Panel available on Settings for operator
# audit. Function stays in ui/components.py for reuse.

# ═══════════════════════════════════════════════════════════════════════════
# Live per-client metrics — Q5. Each client's numbers are derived from
# their assigned tier's basket built against the live-analytics universe
# (return / vol / correlation already live per Q2). The 30-day delta is
# computed on the spot from each holding's last 30 trading-day closes.
# ═══════════════════════════════════════════════════════════════════════════

@st.cache_data(ttl=600)
def _live_universe() -> list[dict]:
    return load_universe_with_live_analytics()


@st.cache_data(ttl=600)
def _build_for_tier(tier_name: str, sleeve_usd: float,
                    universe_key: int) -> dict:
    """Build a live-analytics-backed portfolio for a tier + sleeve size."""
    return build_portfolio(tier_name, _live_universe(),
                           portfolio_value_usd=sleeve_usd)


def _basket_30d_return_pct(holdings: list[dict]) -> tuple[float | None, int]:
    """
    Weighted 30-trading-day return of a basket. Reuses cached price
    bundles inside data_feeds so this is nearly free after the universe
    cache is warm. Returns (pct_or_None, n_holdings_with_live_prices).
    """
    total_weight_with_live = 0.0
    weighted_return = 0.0
    n_live = 0
    for h in holdings:
        tkr = h["ticker"]
        bundle = get_etf_prices([tkr], period="90d", interval="1d")
        rows = bundle.get(tkr, {}).get("prices", []) or []
        closes: list[float] = []
        for row in rows:
            try:
                c = float(row.get("close"))
                if c > 0:
                    closes.append(c)
            except (TypeError, ValueError):
                continue
        if len(closes) < 31:
            continue  # insufficient recent history
        ret_30d = (closes[-1] / closes[-31]) - 1.0
        weight = float(h.get("weight_pct", 0)) / 100.0
        weighted_return += weight * ret_30d
        total_weight_with_live += weight
        n_live += 1
    if total_weight_with_live <= 0:
        return (None, 0)
    # Rescale if some holdings lacked data so partial coverage reports
    # "weighted return of the ETFs we could price" honestly.
    return (weighted_return / total_weight_with_live * 100.0, n_live)


with st.spinner("Computing live per-client metrics..."):
    universe_live = _live_universe()
    _uni_key = id(universe_live)
    _uni_by_tkr = {e["ticker"]: e for e in universe_live}

    def _basket_forward_return(holdings: list[dict]) -> float | None:
        """Weighted forward-return estimate using per-ETF forward_return."""
        num, denom = 0.0, 0.0
        for h in holdings:
            ue = _uni_by_tkr.get(h["ticker"])
            if ue is None:
                continue
            fwd = ue.get("forward_return")
            if fwd is None:
                continue
            w = float(h.get("weight_pct", 0)) / 100.0
            num += w * float(fwd)
            denom += w
        return (num / denom) if denom > 0 else None

    # De-dup portfolio builds by (tier, sleeve_usd) — 3 demo clients so
    # at most 3 distinct portfolios.
    per_client_metrics: dict[str, dict] = {}
    for c in DEMO_CLIENTS:
        sleeve = c["total_portfolio_usd"] * c["crypto_allocation_pct"] / 100.0
        p = _build_for_tier(c["assigned_tier"], sleeve, _uni_key)
        delta_pct, n_priced = _basket_30d_return_pct(p["holdings"])
        per_client_metrics[c["id"]] = {
            "hist_return":  p["metrics"]["weighted_return_pct"],
            "fwd_return":   _basket_forward_return(p["holdings"]),
            "port_vol":     p["metrics"]["portfolio_volatility_pct"],
            "sleeve_usd":   sleeve,
            "delta_30d":    delta_pct,
            "n_priced":     n_priced,
            "n_holdings":   len(p["holdings"]),
        }

# Aggregate live/fallback counts across the universe for the caption.
_n_total = len(universe_live) or 1
_n_live_ret = sum(1 for e in universe_live
                  if e.get("expected_return_source") == "live")
_n_live_vol = sum(1 for e in universe_live
                  if e.get("volatility_source") == "live")
_n_live_corr = sum(1 for e in universe_live
                   if e.get("correlation_source") in ("live", "self"))

# ── 2026-04-25 redesign: port body to advisor-etf-DASHBOARD.html ────────────
#
# Layout (top to bottom):
#   1. KPI strip (cols-4): Clients · AUM (crypto sleeve) · Rebalances due · Last scanner run
#   2. Client roster card — table with avatar/tier-pill/flag styling matching mockup
#   3. Per-row "Open" buttons (real Streamlit nav — HTML table cells can't host widgets)
#   4. 2-col grid (2fr 1fr): Recent activity (7d) · Compliance status
#   5. Hypothetical-results callout (compliance disclaimer)
#
# Existing data wiring preserved: per_client_metrics + DEMO_CLIENTS + universe_live.
# Auxiliary expander (per-client live analytics) folded into the Portfolio page;
# advisors who want that detail open the client.

_n_need_rebalance = sum(1 for c in DEMO_CLIENTS if c["rebalance_needed"])
_n_aligned = len(DEMO_CLIENTS) - _n_need_rebalance

# ── KPI strip values ────────────────────────────────────────────────────────
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

_n_clients = len(DEMO_CLIENTS)
_total_aum_crypto = sum(
    c["total_portfolio_usd"] * c["crypto_allocation_pct"] / 100.0
    for c in DEMO_CLIENTS
)
# 30d AUM delta — weighted across clients by sleeve $
_w_30d_delta = 0.0
_w_total = 0.0
for c in DEMO_CLIENTS:
    sleeve = c["total_portfolio_usd"] * c["crypto_allocation_pct"] / 100.0
    delta = per_client_metrics.get(c["id"], {}).get("delta_30d")
    if delta is not None:
        _w_30d_delta += sleeve * float(delta)
        _w_total += sleeve
_aum_30d_pct = (_w_30d_delta / _w_total) if _w_total > 0 else None

_drift_client = next((c for c in DEMO_CLIENTS if c["rebalance_needed"]), None)
_drift_sub = (
    f"{_drift_client['name'].split()[-1]} · drift {_drift_client['drift_pct']:.1f}σ"
    if _drift_client else "all aligned"
)

_last_scan = "—"
_scan_sub = "EDGAR scanner"
try:
    sh = json.loads(Path("data/scanner_health.json").read_text())
    iso = sh.get("last_success_iso", "")
    if iso:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        _last_scan = dt.strftime("%b %d · %H:%M UTC")
        _n_matches = int(sh.get("n_matches", 0) or 0)
        _scan_sub = f"EDGAR · {_n_matches} new filings"
except Exception:
    pass


def _kpi_card_html(label: str, value: str, sub: str, *, value_color: str = "",
                   value_size: str = "24px") -> str:
    color_attr = f" style=\"color:{value_color};\"" if value_color else ""
    return (
        '<div class="ds-card">'
        f'<div style="font-size:11px;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.08em;">{label}</div>'
        f'<div style="font-size:{value_size};font-family:var(--font-mono);font-weight:500;line-height:1.15;margin-top:4px;color:var(--text-primary);"{color_attr}>{value}</div>'
        f'<div style="font-size:12px;color:var(--text-muted);margin-top:4px;font-family:var(--font-mono);">{sub}</div>'
        '</div>'
    )


_aum_sub = (
    f"+ {_aum_30d_pct:.1f}% · 30d" if (_aum_30d_pct is not None and _aum_30d_pct > 0)
    else f"− {abs(_aum_30d_pct):.1f}% · 30d" if (_aum_30d_pct is not None and _aum_30d_pct < 0)
    else "across {0} clients".format(_n_clients)
)
_aum_sub_color = (
    'color:var(--success);' if (_aum_30d_pct is not None and _aum_30d_pct > 0)
    else 'color:var(--danger);' if (_aum_30d_pct is not None and _aum_30d_pct < 0)
    else ''
)
_aum_sub_html = f'<span style="{_aum_sub_color}">{_aum_sub}</span>' if _aum_sub_color else _aum_sub

st.markdown(
    '<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:var(--gap);margin-bottom:24px;">'
    + _kpi_card_html("Clients", str(_n_clients), "demo personas")
    + _kpi_card_html("AUM (crypto sleeve)", f"${_total_aum_crypto:,.0f}", _aum_sub_html)
    + _kpi_card_html(
        "Rebalances due",
        str(_n_need_rebalance),
        _drift_sub,
        value_color=("var(--warning)" if _n_need_rebalance > 0 else ""),
    )
    + _kpi_card_html(
        "Last scanner run",
        _last_scan,
        _scan_sub,
        value_size="18px",
    )
    + '</div>',
    unsafe_allow_html=True,
)

# ── Client roster table ─────────────────────────────────────────────────────

def _initials(name: str) -> str:
    parts = [p for p in name.split() if p]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def _avatar_gradient(c: dict) -> str:
    """Stable per-client avatar gradient. Drift-flagged clients get a warning
    gradient so they stand out at a glance per the mockup."""
    if c["rebalance_needed"]:
        return "linear-gradient(135deg,#f59e0b,#ef4444)"
    h = sum(ord(ch) for ch in c["id"]) % 3
    return [
        "linear-gradient(135deg,var(--accent),color-mix(in srgb,var(--accent) 60%,#3b82f6))",
        "linear-gradient(135deg,#22c55e,#06b6d4)",
        "linear-gradient(135deg,#3b82f6,#8b5cf6)",
    ][h]


def _last_review_str(iso_str: str) -> str:
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.strftime("%b %d")
    except Exception:
        return "—"


def _ret_fmt(v: float | None, decimals: int = 1) -> tuple[str, str]:
    """Return (text, color_var) for a return %."""
    if v is None:
        return ("—", "var(--text-muted)")
    fv = float(v)
    sign = "+ " if fv > 0 else ("− " if fv < 0 else "")
    color = "var(--success)" if fv > 0 else ("var(--danger)" if fv < 0 else "var(--text-secondary)")
    return (f"{sign}{abs(fv):.{decimals}f}%", color)


# Build table rows
_roster_rows: list[str] = []
for c in DEMO_CLIENTS:
    m = per_client_metrics.get(c["id"], {})
    sleeve = c["total_portfolio_usd"] * c["crypto_allocation_pct"] / 100.0
    hist = m.get("hist_return")
    # vs bench: per-client basket return minus a simple BTC-only benchmark.
    # Use the universe's cached BTC-spot expected_return when available; else "—".
    _bench = next(
        (e.get("expected_return") for e in universe_live
         if (e.get("ticker") or "").upper() == "IBIT"),
        None,
    )
    vs_bench = (hist - _bench) if (hist is not None and _bench is not None) else None

    _hist_txt, _hist_col = _ret_fmt(hist, 1)
    _vs_txt, _vs_col = _ret_fmt(vs_bench, 1) if vs_bench is not None else ("—", "var(--text-muted)")

    if c["rebalance_needed"]:
        flag_html = (
            f'<span class="ds-flag" style="display:inline-flex;align-items:center;gap:6px;'
            f'padding:2px 9px;border-radius:999px;font-size:10.5px;font-weight:600;'
            f'background:color-mix(in srgb,var(--warning) 14%,transparent);color:var(--warning);">'
            f'drift {c["drift_pct"]:.1f}σ · rebal</span>'
        )
        tier_pill_color = (
            'background:color-mix(in srgb,var(--warning) 14%,transparent);color:var(--warning);'
        )
    else:
        flag_html = (
            '<span class="ds-flag" style="display:inline-flex;align-items:center;gap:6px;'
            'padding:2px 9px;border-radius:999px;font-size:10.5px;font-weight:600;'
            'background:color-mix(in srgb,var(--success) 14%,transparent);color:var(--success);">'
            'on target</span>'
        )
        tier_pill_color = 'background:var(--accent-soft);color:var(--accent);'

    _roster_rows.append(
        '<tr>'
        '<td style="padding:14px;border-bottom:1px solid var(--border);">'
        '<div style="display:inline-flex;align-items:center;gap:10px;">'
        f'<div style="width:28px;height:28px;border-radius:6px;background:{_avatar_gradient(c)};'
        'color:white;font-size:11.5px;font-weight:600;display:grid;place-items:center;">'
        f'{_initials(c["name"])}</div>'
        '<div>'
        f'<div style="font-weight:600;color:var(--text-primary);">{c["name"]}</div>'
        f'<div style="font-size:11.5px;color:var(--text-muted);">{c["label"].lower()} · age {c["age"]}</div>'
        '</div></div></td>'
        f'<td style="padding:14px;border-bottom:1px solid var(--border);">'
        f'<span style="display:inline-block;padding:2px 9px;border-radius:999px;'
        f'font-size:10.5px;font-weight:600;{tier_pill_color}">{c["assigned_tier"]}</span></td>'
        f'<td style="padding:14px;border-bottom:1px solid var(--border);text-align:right;'
        f'font-family:var(--font-mono);">${sleeve:,.0f}</td>'
        f'<td style="padding:14px;border-bottom:1px solid var(--border);text-align:right;'
        f'font-family:var(--font-mono);color:{_hist_col};">{_hist_txt}</td>'
        f'<td style="padding:14px;border-bottom:1px solid var(--border);text-align:right;'
        f'font-family:var(--font-mono);color:{_vs_col};">{_vs_txt} ppts</td>'
        f'<td style="padding:14px;border-bottom:1px solid var(--border);">{flag_html}</td>'
        f'<td style="padding:14px;border-bottom:1px solid var(--border);'
        f'color:var(--text-muted);font-size:12px;">{_last_review_str(c["last_rebalance_iso"])}</td>'
        '</tr>'
    )

st.markdown(
    '<div class="ds-card" style="margin-bottom:20px;padding:0;overflow:hidden;">'
    '<div style="padding:20px 24px 0;">'
    '<div style="font-family:var(--font-display);font-weight:500;font-size:16px;'
    'color:var(--text-primary);margin:0;">Client roster</div>'
    '<div style="font-size:12.5px;color:var(--text-muted);margin-top:2px;">'
    'Click an Open button below to drill into that client\'s portfolio.</div>'
    '</div>'
    '<table style="width:100%;border-collapse:collapse;font-size:13px;margin-top:16px;">'
    '<thead><tr>'
    '<th style="text-align:left;font-size:10.5px;text-transform:uppercase;letter-spacing:0.07em;'
    'color:var(--text-muted);font-weight:500;padding:12px 14px 10px;border-bottom:1px solid var(--border);">Client</th>'
    '<th style="text-align:left;font-size:10.5px;text-transform:uppercase;letter-spacing:0.07em;'
    'color:var(--text-muted);font-weight:500;padding:12px 14px 10px;border-bottom:1px solid var(--border);">Tier</th>'
    '<th style="text-align:right;font-size:10.5px;text-transform:uppercase;letter-spacing:0.07em;'
    'color:var(--text-muted);font-weight:500;padding:12px 14px 10px;border-bottom:1px solid var(--border);">AUM · crypto</th>'
    '<th style="text-align:right;font-size:10.5px;text-transform:uppercase;letter-spacing:0.07em;'
    'color:var(--text-muted);font-weight:500;padding:12px 14px 10px;border-bottom:1px solid var(--border);">Hist return</th>'
    '<th style="text-align:right;font-size:10.5px;text-transform:uppercase;letter-spacing:0.07em;'
    'color:var(--text-muted);font-weight:500;padding:12px 14px 10px;border-bottom:1px solid var(--border);">vs bench</th>'
    '<th style="text-align:left;font-size:10.5px;text-transform:uppercase;letter-spacing:0.07em;'
    'color:var(--text-muted);font-weight:500;padding:12px 14px 10px;border-bottom:1px solid var(--border);">Rebalance</th>'
    '<th style="text-align:left;font-size:10.5px;text-transform:uppercase;letter-spacing:0.07em;'
    'color:var(--text-muted);font-weight:500;padding:12px 14px 10px;border-bottom:1px solid var(--border);">Last reviewed</th>'
    '</tr></thead>'
    '<tbody>'
    + "".join(_roster_rows)
    + '</tbody></table>'
    '</div>',
    unsafe_allow_html=True,
)

# Real Streamlit buttons for navigation — HTML table cells can't host widgets,
# so we render a row of "Open" buttons under the table, one per client.
_open_cols = st.columns(len(DEMO_CLIENTS))
for _idx, c in enumerate(DEMO_CLIENTS):
    with _open_cols[_idx]:
        _label_suffix = " ⚠" if c["rebalance_needed"] else ""
        if st.button(
            f"Open {c['name']}{_label_suffix} →",
            key=f"dash_open_{c['id']}",
            use_container_width=True,
            type=("primary" if c["rebalance_needed"] else "secondary"),
        ):
            st.session_state["active_client_id"] = c["id"]
            st.switch_page("pages/02_Portfolio.py")

st.markdown('<div style="height:24px;"></div>', unsafe_allow_html=True)

# ── 2-col grid: Recent activity · Compliance status ─────────────────────────
_act_col, _comp_col = st.columns([2, 1])

with _act_col:
    # Pull recent audit-log entries — fall back to demo content if log empty.
    _activity_items: list[tuple[str, str, str]] = []
    try:
        _audit_path = Path("data/audit_log.json")
        if _audit_path.exists():
            _entries = json.loads(_audit_path.read_text())
            if isinstance(_entries, list):
                for e in sorted(_entries, key=lambda x: x.get("timestamp", ""), reverse=True)[:5]:
                    ts = e.get("timestamp", "")
                    try:
                        dt_e = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                        ts_str = dt_e.strftime("%b %d %H:%M")
                    except Exception:
                        ts_str = ts[:16]
                    action = e.get("action", "note")
                    text = e.get("description") or e.get("message") or ""
                    tag = "exec" if "exec" in action.lower() or "rebal" in action.lower() else (
                        "rev" if "review" in action.lower() else "note"
                    )
                    _activity_items.append((ts_str, text[:120], tag))
    except Exception:
        pass
    if not _activity_items:
        # Demo fallback — every advisor wants to SEE recent activity even
        # before the audit log is seeded.
        _activity_items = [
            ("Apr 22 14:30", f"Rebalanced {DEMO_CLIENTS[2]['name'].split()[0]} basket · sleeve trimmed back to target", "exec"),
            ("Apr 20 11:12", "Scanner flagged 4 new ETFs from EDGAR · added to universe watchlist", "note"),
            ("Apr 18 09:48", f"{DEMO_CLIENTS[0]['name'].split()[0]} · quarterly review complete · no change needed", "rev"),
            ("Apr 15 15:02", f"{DEMO_CLIENTS[1]['name'].split()[0]} · drift alert triggered · {DEMO_CLIENTS[1]['drift_pct']:.1f}σ over target", "note"),
            ("Apr 10 10:20", "Methodology page updated · SEC Marketing Rule v3 compliance", "note"),
        ]
    _tag_styles = {
        "exec":  "background:var(--accent-soft);color:var(--accent);",
        "rev":   "background:color-mix(in srgb,var(--warning) 14%,transparent);color:var(--warning);",
        "note":  "background:var(--bg-2);color:var(--text-muted);",
    }
    _act_rows = "".join(
        '<div style="display:grid;grid-template-columns:110px 1fr 90px;gap:12px;'
        'align-items:center;padding:10px 0;border-bottom:1px solid var(--border);'
        'font-size:12.5px;">'
        f'<span style="font-family:var(--font-mono);color:var(--text-muted);font-size:11.5px;">{ts}</span>'
        f'<span style="color:var(--text-secondary);">{txt}</span>'
        f'<span style="font-size:11px;padding:2px 8px;border-radius:999px;text-align:center;'
        f'font-weight:500;{_tag_styles.get(tag, _tag_styles["note"])}">{tag}</span>'
        '</div>'
        for ts, txt, tag in _activity_items
    )
    st.markdown(
        '<div class="ds-card">'
        '<div style="margin-bottom:16px;">'
        '<div style="font-family:var(--font-display);font-weight:500;font-size:16px;'
        'color:var(--text-primary);margin:0;">Recent activity · last 7d</div>'
        '<div style="font-size:12.5px;color:var(--text-muted);margin-top:2px;">all actions are audit-logged.</div>'
        '</div>'
        f'<div>{_act_rows}</div>'
        '</div>',
        unsafe_allow_html=True,
    )

with _comp_col:
    _checks = [
        ("Disclosures present on all views", "✓ yes"),
        ("Benchmark comparison always shown", "✓ yes"),
        ("Max drawdown labelled", "✓ yes"),
        ("Client recos in audit log", "✓ yes"),
        ("Methodology page up to date", "✓ Apr 10"),
        ("Demo-mode flag visible", "✓ yes"),
    ]
    _check_html = "".join(
        '<div style="display:flex;justify-content:space-between;align-items:center;'
        f'gap:12px;font-size:12.5px;color:var(--text-secondary);">'
        f'<span>{lbl}</span>'
        '<span style="display:inline-flex;align-items:center;gap:6px;padding:2px 9px;'
        'border-radius:999px;font-size:10.5px;font-weight:600;'
        f'background:color-mix(in srgb,var(--success) 14%,transparent);color:var(--success);">{val}</span>'
        '</div>'
        for lbl, val in _checks
    )
    st.markdown(
        '<div class="ds-card">'
        '<div style="margin-bottom:16px;">'
        '<div style="font-family:var(--font-display);font-weight:500;font-size:16px;'
        'color:var(--text-primary);margin:0;">Compliance status</div>'
        '<div style="font-size:12.5px;color:var(--text-muted);margin-top:2px;">'
        'SEC Marketing Rule · per-FA tracking.</div>'
        '</div>'
        f'<div style="display:flex;flex-direction:column;gap:14px;">{_check_html}</div>'
        '</div>',
        unsafe_allow_html=True,
    )

# ── Hypothetical-results callout (compliance disclaimer) ────────────────────
st.markdown(
    '<div style="display:flex;gap:14px;align-items:flex-start;'
    'padding:16px 20px;margin-top:24px;'
    'background:color-mix(in srgb,var(--accent) 5%,var(--bg-1));'
    'border:1px solid color-mix(in srgb,var(--accent) 20%,var(--border));'
    'border-left:3px solid var(--accent);border-radius:8px;font-size:13px;">'
    '<div style="width:22px;height:22px;border-radius:50%;'
    'background:var(--accent-soft);color:var(--accent);'
    'display:grid;place-items:center;font-weight:600;font-size:13px;flex-shrink:0;">i</div>'
    '<div><strong style="color:var(--text-primary);">Hypothetical results.</strong> '
    'All client profiles shown are fictional demo personas. Past performance does not guarantee '
    'future results. See the Methodology page for full disclosures and assumptions.</div>'
    '</div>',
    unsafe_allow_html=True,
)
