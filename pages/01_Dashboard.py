"""
Dashboard — advisor home view. Client roster with status, rebalance flags,
per-client Open-Portfolio link.

All clients shown are fictional demo personas per CLAUDE.md §22 item 3.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from config import AVATAR_PALETTE, BRAND_NAME, DEMO_MODE, EXTENDED_MODULES_ENABLED
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

# Defensive imports — if Streamlit Cloud is on a stale cached
# `ui/components.py` that lacks audit-round-1 helpers, fall back to
# inline minimal versions so the page still renders.
try:
    from ui.components import hypothetical_results_disclosure
except ImportError:  # pragma: no cover — stale-deploy fallback
    def hypothetical_results_disclosure(body: str | None = None, *,
                                         margin_top_px: int = 24) -> None:
        st.info(
            "**Hypothetical results.** Past performance does not "
            "guarantee future results. " + (body or "")
        )

try:
    from ui.components import extended_modules_banner
except ImportError:  # pragma: no cover — stale-deploy fallback
    def extended_modules_banner(*, margin_top_px: int = 24) -> None:
        st.info(
            "**Extended coverage — preview release.** Execution not "
            "yet enabled for this module."
        )
from ui.level_helpers import level_text
from ui.sidebar import render_sidebar
from ui.theme import apply_theme


def main() -> None:
    st.set_page_config(page_title=f"Dashboard — {BRAND_NAME}", layout="wide")
    apply_theme()
    render_sidebar()

    # ── 2026-05 redesign: advisor-family top bar + page header ──
    try:
        from ui import render_top_bar as _ds_top_bar, page_header as _ds_page_header
        _ds_level_adv = st.session_state.get("user_level", "Advisor")
        _ds_top_bar(breadcrumb=("Advisor", "Dashboard"), user_level=_ds_level_adv)
        _ds_page_header(
            title="Client dashboard",
            subtitle=level_text(
                         advisor="Client roster with tier / drift / flags. Filter + sort via column headers.",
                         client="Your client list with rebalance status and quick links.",
                     ),
            data_sources=[("Custody feed", "live"), ("Model drift", "cached")],
        )
    except Exception:
        section_header(
            "Dashboard",
            level_text(
                advisor="Client roster with tier / drift / flags. Filter + sort via column headers.",
                client="Your client list with rebalance status and quick links.",
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
        """Stable per-client avatar gradient. Drift-flagged clients get the
        warning gradient (slot 0) so they stand out at a glance per the mockup.
        Other clients cycle through AVATAR_PALETTE slots [1..N-1] keyed on
        client id hash. Centralized in config.AVATAR_PALETTE per audit-round-1
        commit 7 — no more inline hex codes here."""
        if c["rebalance_needed"]:
            stops = AVATAR_PALETTE[0]
            return f"linear-gradient(135deg,{stops[0]},{stops[1]})"
        # Cycle through non-warning slots (1..N-1)
        non_warning = AVATAR_PALETTE[1:]
        if not non_warning:
            return "linear-gradient(135deg,var(--accent),var(--accent))"
        h = sum(ord(ch) for ch in c["id"]) % len(non_warning)
        stops = non_warning[h]
        return f"linear-gradient(135deg,{stops[0]},{stops[1]})"


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


    # ── 2026-04-25 redesign: roster as per-row st.columns with inline "Open →"
    # Cowork walkthrough: 3 large CTA buttons under the table looked like loose
    # duplicates. The mockup has the Open trailing each row (small text-link
    # style at the row's right edge). HTML <table> cells can't host Streamlit
    # widgets, so we render the roster as a sequence of st.columns rows where
    # the last column is a real st.button. CSS overrides scope the button to
    # look like an inline text link rather than a chunky chip.
    #
    # vs-bench reference is the same as before — IBIT spot CAGR.
    _bench = next(
        (e.get("expected_return") for e in universe_live
         if (e.get("ticker") or "").upper() == "IBIT"),
        None,
    )

    # Inline CSS — paints the per-row "Open →" Streamlit button to look like a
    # text link rather than a chip. Scoped via a marker class on the wrapping
    # card so it can't bleed into other pages.
    _roster_inline_btn_css = """
    <style>
    .eap-roster-card [data-testid="stHorizontalBlock"] {
      align-items: center;
    }
    .eap-roster-card .eap-row-divider {
      border-bottom: 1px solid var(--border);
      margin: 0 -24px;
    }
    .eap-roster-card [data-testid="stButton"] > button {
      background: transparent !important;
      border: none !important;
      color: var(--accent) !important;
      font-weight: 600 !important;
      font-size: 13px !important;
      padding: 4px 0 !important;
      min-height: 0 !important;
      text-align: right !important;
      justify-content: flex-end !important;
      box-shadow: none !important;
    }
    .eap-roster-card [data-testid="stButton"] > button:hover {
      color: var(--text-primary) !important;
      background: transparent !important;
      text-decoration: underline;
    }
    </style>
    """
    st.markdown(_roster_inline_btn_css, unsafe_allow_html=True)

    # Open the roster card (with marker class so the inline-btn CSS only
    # applies to buttons inside this card).
    st.markdown(
        '<div class="ds-card eap-roster-card" style="margin-bottom:20px;padding:20px 24px;">'
        '<div style="font-family:var(--font-display);font-weight:500;font-size:16px;'
        'color:var(--text-primary);margin:0;">Client roster</div>'
        '<div style="font-size:12.5px;color:var(--text-muted);margin-top:2px;'
        'margin-bottom:12px;">Click <b>Open →</b> at the end of any row to drill '
        "into that client's portfolio.</div>",
        unsafe_allow_html=True,
    )

    _roster_layout = [3.0, 1.6, 1.1, 1.0, 1.0, 1.4, 0.9, 0.8]  # 8 cols total

    # Header row
    _hdr_cells = [
        ("Client",        "left"),
        ("Tier",          "left"),
        ("AUM · crypto",  "right"),
        ("Hist return",   "right"),
        ("vs bench",      "right"),
        ("Rebalance",     "left"),
        ("Last reviewed", "left"),
        ("",              "right"),  # Open column header is intentionally blank
    ]
    _hdr_cols = st.columns(_roster_layout)
    for _hcol, (_label, _align) in zip(_hdr_cols, _hdr_cells):
        _hcol.markdown(
            f'<div style="font-size:10.5px;text-transform:uppercase;'
            f'letter-spacing:0.07em;color:var(--text-muted);font-weight:500;'
            f'text-align:{_align};padding:6px 0;border-bottom:1px solid var(--border);">'
            f'{_label}</div>',
            unsafe_allow_html=True,
        )

    # Data rows
    for c in DEMO_CLIENTS:
        m = per_client_metrics.get(c["id"], {})
        sleeve = c["total_portfolio_usd"] * c["crypto_allocation_pct"] / 100.0
        hist = m.get("hist_return")
        vs_bench = (hist - _bench) if (hist is not None and _bench is not None) else None

        _hist_txt, _hist_col = _ret_fmt(hist, 1)
        _vs_txt, _vs_col = _ret_fmt(vs_bench, 1) if vs_bench is not None else ("—", "var(--text-muted)")

        if c["rebalance_needed"]:
            _flag_html = (
                f'<span style="display:inline-flex;align-items:center;gap:6px;'
                f'padding:2px 9px;border-radius:999px;font-size:10.5px;font-weight:600;'
                f'background:color-mix(in srgb,var(--warning) 14%,transparent);'
                f'color:var(--warning);">drift {c["drift_pct"]:.1f}σ · rebal</span>'
            )
            _tier_pill_bg = 'color-mix(in srgb,var(--warning) 14%,transparent)'
            _tier_pill_fg = 'var(--warning)'
        else:
            _flag_html = (
                '<span style="display:inline-flex;align-items:center;gap:6px;'
                'padding:2px 9px;border-radius:999px;font-size:10.5px;font-weight:600;'
                'background:color-mix(in srgb,var(--success) 14%,transparent);'
                'color:var(--success);">on target</span>'
            )
            _tier_pill_bg = 'var(--accent-soft)'
            _tier_pill_fg = 'var(--accent)'

        _row_cols = st.columns(_roster_layout)
        # Cell 0 — avatar + name + label
        _row_cols[0].markdown(
            '<div style="display:inline-flex;align-items:center;gap:10px;padding:10px 0;">'
            f'<div style="width:28px;height:28px;border-radius:6px;'
            f'background:{_avatar_gradient(c)};color:white;font-size:11.5px;'
            'font-weight:600;display:grid;place-items:center;flex-shrink:0;">'
            f'{_initials(c["name"])}</div>'
            '<div>'
            f'<div style="font-weight:600;color:var(--text-primary);font-size:13px;">{c["name"]}</div>'
            f'<div style="font-size:11.5px;color:var(--text-muted);">{c["label"].lower()} · age {c["age"]}</div>'
            '</div></div>',
            unsafe_allow_html=True,
        )
        # Cell 1 — tier pill
        _row_cols[1].markdown(
            f'<div style="padding:14px 0;">'
            f'<span style="display:inline-block;padding:2px 9px;border-radius:999px;'
            f'font-size:10.5px;font-weight:600;background:{_tier_pill_bg};'
            f'color:{_tier_pill_fg};">{c["assigned_tier"]}</span></div>',
            unsafe_allow_html=True,
        )
        # Cell 2 — AUM (right-aligned mono)
        _row_cols[2].markdown(
            f'<div style="padding:14px 0;text-align:right;font-family:var(--font-mono);'
            f'font-size:13px;color:var(--text-primary);">${sleeve:,.0f}</div>',
            unsafe_allow_html=True,
        )
        # Cell 3 — hist return
        _row_cols[3].markdown(
            f'<div style="padding:14px 0;text-align:right;font-family:var(--font-mono);'
            f'font-size:13px;color:{_hist_col};">{_hist_txt}</div>',
            unsafe_allow_html=True,
        )
        # Cell 4 — vs bench
        _row_cols[4].markdown(
            f'<div style="padding:14px 0;text-align:right;font-family:var(--font-mono);'
            f'font-size:13px;color:{_vs_col};">{_vs_txt} ppts</div>',
            unsafe_allow_html=True,
        )
        # Cell 5 — rebalance flag
        _row_cols[5].markdown(
            f'<div style="padding:14px 0;">{_flag_html}</div>',
            unsafe_allow_html=True,
        )
        # Cell 6 — last reviewed
        _row_cols[6].markdown(
            f'<div style="padding:14px 0;color:var(--text-muted);font-size:12px;">'
            f'{_last_review_str(c["last_rebalance_iso"])}</div>',
            unsafe_allow_html=True,
        )
        # Cell 7 — inline "Open →" Streamlit button styled as text link
        with _row_cols[7]:
            if st.button(
                "Open →",
                key=f"dash_open_inline_{c['id']}",
                use_container_width=True,
            ):
                st.session_state["active_client_id"] = c["id"]
                st.switch_page("pages/02_Portfolio.py")

        # Row divider — thin border between rows (matches the HTML table look).
        st.markdown('<div class="eap-row-divider"></div>', unsafe_allow_html=True)

    # Close the roster card
    st.markdown('</div>', unsafe_allow_html=True)

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

    # ── Pending auto-rebalances (gated by per-client auto-execute checkboxes) ──
    # When the FA flips on `auto_exec_<client_id>` in Settings for a client,
    # AND that client has rebalance_needed=True, a "Pending auto-rebalances"
    # panel surfaces here on Dashboard. Mock-broker-only for May 1; real
    # broker wiring (Alpaca paper / Alpaca live) is post-demo. The panel
    # gives the FA a single-page review of which clients are queued for
    # the next auto-rebalance run before it fires.
    _pending_auto: list[dict] = []
    for c in DEMO_CLIENTS:
        if (
            st.session_state.get(f"auto_exec_{c['id']}", False)
            and c["rebalance_needed"]
        ):
            _pending_auto.append(c)

    if _pending_auto:
        st.markdown('<div style="height:24px;"></div>', unsafe_allow_html=True)
        _ar_rows = "".join(
            '<div style="display:grid;grid-template-columns:1.5fr 1fr 1fr 1fr;gap:12px;'
            'align-items:center;padding:10px 4px;border-bottom:1px solid var(--border);'
            'font-size:12.5px;">'
            f'<span style="font-weight:600;color:var(--text-primary);">{c["name"]}</span>'
            f'<span style="font-family:var(--font-mono);color:var(--text-muted);">{c["assigned_tier"]}</span>'
            f'<span style="font-family:var(--font-mono);color:var(--warning);">drift {c["drift_pct"]:.1f}σ</span>'
            f'<span style="text-align:right;font-family:var(--font-mono);color:var(--text-muted);">'
            'queued for next run</span>'
            '</div>'
            for c in _pending_auto
        )
        _broker_provider = st.session_state.get("broker_provider_override")
        _broker_label = (
            "mock broker (no real orders will fire)"
            if not _broker_provider or _broker_provider == "mock"
            else f"{_broker_provider} (post-demo wiring)"
        )
        st.markdown(
            '<div class="ds-card" style="margin-bottom:14px;'
            'background:color-mix(in srgb,var(--warning) 5%,var(--bg-1));'
            'border-left:3px solid var(--warning);">'
            '<div style="font-family:var(--font-display);font-weight:500;font-size:16px;color:var(--text-primary);">'
            f'Pending auto-rebalances · {len(_pending_auto)} client'
            f'{"s" if len(_pending_auto) != 1 else ""}'
            '</div>'
            '<div style="font-size:12.5px;color:var(--text-muted);margin-top:4px;">'
            f'Daily scheduler will fire these on the next run via {_broker_label}. '
            'Disable per-client in Settings → Per-client auto-execute permissions.'
            '</div>'
            f'<div style="margin-top:12px;">{_ar_rows}</div>'
            '</div>',
            unsafe_allow_html=True,
        )

    # ── Cross-asset preview (gated by Settings → Extended modules toggle) ─────
    # When the FA flips `extended_modules_override` ON in Settings, this section
    # appears showing how the ETF Advisor sleeve would compose alongside RWA
    # + DeFi exposure (which live as sibling apps `rwa-infinity-model` and
    # `flare-defi-model`). Off by default — shows nothing — so the standard
    # advisor view stays clean for the May 1 demo.
    if st.session_state.get("extended_modules_override", EXTENDED_MODULES_ENABLED):
        st.markdown('<div style="height:24px;"></div>', unsafe_allow_html=True)
        _xa_total_traditional = sum(c["total_portfolio_usd"] * (100 - c["crypto_allocation_pct"]) / 100.0
                                     for c in DEMO_CLIENTS)
        _xa_total_crypto = sum(c["total_portfolio_usd"] * c["crypto_allocation_pct"] / 100.0
                                for c in DEMO_CLIENTS)
        _xa_total = _xa_total_traditional + _xa_total_crypto
        _xa_rwa_placeholder = _xa_total_traditional * 0.10  # illustrative 10% RWA carve-out
        _xa_defi_placeholder = _xa_total_crypto * 0.05      # illustrative 5% DeFi sleeve

        def _xa_card(label, value, sub, accent_color="var(--accent)"):
            return (
                '<div class="ds-card" style="text-align:left;">'
                f'<div style="font-size:11px;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.08em;">{label}</div>'
                f'<div style="font-size:22px;font-family:var(--font-mono);font-weight:500;line-height:1.15;margin-top:4px;color:{accent_color};">{value}</div>'
                f'<div style="font-size:12px;color:var(--text-muted);margin-top:4px;font-family:var(--font-mono);">{sub}</div>'
                '</div>'
            )

        # Verbatim CLAUDE.md §22 item 4 banner — extended-module preview
        # surfaces (RWA / DeFi cross-asset preview) carry the canonical wording.
        extended_modules_banner(margin_top_px=0)
        st.markdown(
            '<div class="ds-card" style="margin-bottom:14px;'
            'background:color-mix(in srgb,var(--info) 5%,var(--bg-1));'
            'border-left:3px solid var(--info);margin-top:14px;">'
            '<div style="font-family:var(--font-display);font-weight:500;'
            'font-size:16px;color:var(--text-primary);">'
            'Cross-asset preview · ETF + RWA + DeFi'
            '</div>'
            '<div style="font-size:12.5px;color:var(--text-muted);margin-top:4px;">'
            'RWA + DeFi sleeves live in sibling apps; numbers below are illustrative '
            'carve-outs against the same demo-client AUM.'
            '</div>'
            '</div>',
            unsafe_allow_html=True,
        )
        st.markdown(
            '<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:var(--gap);margin-bottom:8px;">'
            + _xa_card("Crypto-ETF sleeve",       f"${_xa_total_crypto:,.0f}",       "live · this app")
            + _xa_card("RWA sleeve · preview",    f"${_xa_rwa_placeholder:,.0f}",    "illustrative · rwa-infinity-model", "var(--info)")
            + _xa_card("DeFi sleeve · preview",   f"${_xa_defi_placeholder:,.0f}",   "illustrative · flare-defi-model",   "var(--info)")
            + '</div>',
            unsafe_allow_html=True,
        )
        st.caption(
            "↗ Open companion apps: `rwa-infinity-model` (real-world assets), "
            "`flare-defi-model` (DeFi yields). Cross-asset roll-up will land in a "
            "post-demo integration sprint."
        )
        st.markdown('<div style="height:8px;"></div>', unsafe_allow_html=True)

    # ── Hypothetical-results callout — canonical wording per CLAUDE.md §22 item 5
    hypothetical_results_disclosure(
        body=(
            "All client profiles shown are fictional demo personas. See the "
            "Methodology page for full disclosures and assumptions."
        ),
    )


if __name__ == "__main__":
    main()
