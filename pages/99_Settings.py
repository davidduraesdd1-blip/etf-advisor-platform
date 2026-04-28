"""
Settings — feature flags readback, broker routing, monitoring preferences,
per-client auto-execute toggles, scanner health indicator.

Scanner health comes from core.etf_universe.get_scanner_health() which
reads data/scanner_health.json (written atomically on every successful
daily_scanner run, per Day-3 item B).
"""
from __future__ import annotations

from typing import Optional

import streamlit as st

from config import (
    ALPACA_BASE_URL as ALPACA_BASE_URL_DISPLAY,
    BRAND_NAME,
    BROKER_PROVIDER,
    DEMO_MODE,
    EDGAR_CONTACT_EMAIL,
    EXTENDED_MODULES_ENABLED,
    ETF_PRICE_SOURCE,
    ETF_REFERENCE_SOURCE,
)
from core.audit_log import recent_entries
from core.data_source_state import snapshot as dss_snapshot
# Sprint 3: client roster panel reads from the active adapter so a
# CRM-connected deploy shows real clients, not the demo trio.
from core.client_adapter import (
    get_active_adapter,
    get_active_clients,
    get_adapter,
    list_registered_providers,
)
from core.etf_universe import SCANNER_STALE_HOURS, get_scanner_health
from integrations.data_feeds import circuit_breaker_state, reset_circuit_breaker
from ui.components import card, data_sources_panel, section_header
from ui.level_helpers import level_text
from ui.sidebar import render_sidebar
from ui.theme import apply_theme


def main() -> None:
    st.set_page_config(page_title=f"Settings — {BRAND_NAME}", layout="wide")
    apply_theme()
    render_sidebar()

    try:
        from ui import render_top_bar as _ds_top_bar, page_header as _ds_page_header
        _ds_top_bar(breadcrumb=("Account", "Settings"),
                    user_level=st.session_state.get("user_level", "Advisor"))
        _ds_page_header(
            title="Settings",
            subtitle=level_text(
                         advisor="Runtime flags + data-source state + circuit-breaker controls + per-client overrides.",
                         client="Control how the app fetches data, routes orders, and monitors your portfolios.",
                     ),
        )
    except Exception:
        section_header(
            "Settings",
            level_text(
                advisor="Runtime flags + data-source state + circuit-breaker controls + per-client overrides.",
                client="Control how the app fetches data, routes orders, and monitors your portfolios.",
            ),
        )

    # Top-of-page data-source audit panel (Option 3 transparency).
    # Settings opens it expanded since this is the operator-diagnostic page.
    data_sources_panel(expanded=True, key="ds_panel_settings")


    # ═══════════════════════════════════════════════════════════════════════════
    # Preferences — FA-facing settings
    # ═══════════════════════════════════════════════════════════════════════════
    st.markdown("## Preferences")

    with card("Broker routing"):
        options = ["mock", "alpaca_paper", "alpaca"]
        # Session override wins over config.BROKER_PROVIDER. Lets the FA flip
        # to alpaca_paper for a single demo without a config.py edit.
        active_provider = st.session_state.get("broker_provider_override", BROKER_PROVIDER)
        current_idx = options.index(active_provider) if active_provider in options else 0
        selected = st.selectbox(
            "Provider",
            options=options,
            index=current_idx,
            help="Changing this here is session-only. To change persistently, edit config.py.",
        )
        # Persist the choice so submit_basket_via routes to the chosen broker.
        st.session_state["broker_provider_override"] = selected
        if selected == "alpaca_paper":
            from config import ALPACA_API_KEY, ALPACA_API_SECRET
            # Detect alpaca-py presence — we ship it in requirements.txt
            # but the import is still optional (graceful fallback to mock
            # if pip install fails on a constrained environment).
            try:
                import alpaca  # noqa: F401
                _alpaca_pkg_present = True
            except ImportError:
                _alpaca_pkg_present = False

            if not _alpaca_pkg_present:
                st.warning(
                    "`alpaca-py` SDK isn't importable in this environment. "
                    "Add it to `requirements.txt` (it's there as of "
                    "audit-round-1 — pin `alpaca-py>=0.30.0`) and let "
                    "Streamlit Cloud rebuild. Until then, basket-execute "
                    "falls back to the mock broker."
                )
            elif not ALPACA_API_KEY or not ALPACA_API_SECRET:
                st.info(
                    "Alpaca paper-trading routing is wired and the SDK is "
                    "installed. To enable real paper-trading order "
                    "submission, set `ALPACA_API_KEY` + `ALPACA_API_SECRET`:\n\n"
                    "- **Local dev:** add to `.env` and restart Streamlit.\n"
                    "- **Streamlit Cloud:** Manage app → Settings → Secrets:\n"
                    "  ```toml\n"
                    "  ALPACA_API_KEY = \"PK…\"\n"
                    "  ALPACA_API_SECRET = \"…\"\n"
                    "  ```\n\n"
                    "Until then, basket-execute falls back to the mock "
                    "broker — fallback is recorded in the response "
                    "payload's `broker` field for audit."
                )
            else:
                st.success(
                    "Alpaca paper-trading credentials detected and SDK is "
                    "installed. Basket-execute will submit real paper "
                    f"orders to {ALPACA_BASE_URL_DISPLAY}."
                )
        elif selected == "alpaca":
            st.warning(
                "Live Alpaca routing requires explicit user activation per "
                "CLAUDE.md §11 (Web3 Level B). For now, this routes to the "
                "paper endpoint with a `_pending_live_approval` audit flag."
            )


    # ═══════════════════════════════════════════════════════════════════════════
    # Sprint 4 — Live order streaming (Alpaca TradingStream WebSocket)
    # Surfaces real-time order status events back into the Portfolio page's
    # Recent submissions expander. Configured iff ALPACA_API_KEY_ID +
    # ALPACA_API_SECRET_KEY (or legacy ALPACA_API_KEY/ALPACA_API_SECRET) are
    # both set. Buttons let the FA Start / Stop the daemon thread without
    # restarting the app.
    # ═══════════════════════════════════════════════════════════════════════════

    with card("Live order streaming"):
        try:
            from integrations import alpaca_streaming as _streaming
            _health = _streaming.get_stream_health()
            _stream_err: Optional[str] = None
        except Exception as exc:
            # Audit-fix (LOW): per CLAUDE.md §8, never expose Python
            # tracebacks to users. Settings is operator-scope so we still
            # surface the diagnostic, but behind an "Advanced diagnostics"
            # expander with a plain-English summary first.
            _stream_err = f"{type(exc).__name__}: {exc}"
            st.error(
                "Streaming module failed to import — check that "
                "`alpaca-py>=0.30.0` is in `requirements.txt` and "
                "the deploy bundle. Open the Advanced diagnostics "
                "expander below for the underlying error."
            )
            with st.expander("Advanced diagnostics — streaming import failure"):
                st.code(_stream_err, language="text")
            _health = None

        if _health is not None:
            st.caption(level_text(
                advisor=(
                    "Real-time order status via Alpaca's TradingStream "
                    "WebSocket. Configured when both ALPACA_API_KEY_ID and "
                    "ALPACA_API_SECRET_KEY are set in env / Streamlit Secrets. "
                    "Daemon thread reconnects automatically with exponential "
                    "backoff (1, 2, 4, 8, 16, 30s)."
                ),
                client=(
                    "Watches paper-trading orders in real time so fills "
                    "appear without refreshing."
                ),
            ))

            # Status pill row
            _on = _health["streaming"]
            _conf = _health["configured"]
            if not _conf:
                _pill_color, _pill_label = "#9ca3af", "Not configured"
            elif _on:
                _pill_color, _pill_label = "#22c55e", "Streaming"
            else:
                _pill_color, _pill_label = "#f59e0b", "Stopped"

            st.markdown(
                f'<div style="display:flex;align-items:center;gap:14px;'
                f'padding:8px 0;font-family:var(--font-mono);font-size:13px;">'
                f'<span style="display:inline-block;padding:3px 12px;'
                f'border-radius:12px;background:{_pill_color};color:#fff;'
                f'font-size:11px;font-weight:600;">■ {_pill_label}</span>'
                f'<span style="color:var(--text-secondary);">'
                f'Last event: <code>{_health["last_event_iso"] or "—"}</code></span>'
                f'<span style="color:var(--text-secondary);">'
                f'Tracked orders: <code>{_health["tracked_orders"]}</code></span>'
                f'<span style="color:var(--text-muted);">'
                f'Reconnects: <code>{_health["reconnect_attempts"]}</code></span>'
                f'</div>',
                unsafe_allow_html=True,
            )

            # Surface the last error explicitly per CLAUDE.md §22 — never
            # silently fall back; if streaming failed, the FA must see why.
            if _health.get("last_error"):
                st.error(f"Last stream error: `{_health['last_error']}`")

            # Start / Stop controls
            _c_start, _c_stop = st.columns(2)
            with _c_start:
                if st.button(
                    "Start streaming",
                    width="stretch",
                    disabled=(not _conf) or _on,
                    help=(
                        "Set ALPACA_API_KEY_ID + ALPACA_API_SECRET_KEY to enable."
                        if not _conf else
                        "Already streaming." if _on else
                        "Start the TradingStream daemon thread."
                    ),
                    # Audit-fix: on_click callback so cache+state mutate
                    # under Streamlit's natural button-click rerun and
                    # the page route is preserved on Streamlit Cloud.
                    on_click=_streaming.start_order_stream,
                ):
                    pass
            with _c_stop:
                if st.button(
                    "Stop streaming",
                    width="stretch",
                    disabled=not _on,
                    help="Gracefully stop the streaming thread.",
                    on_click=_streaming.stop_order_stream,
                ):
                    pass

            if not _conf:
                st.info(
                    "**Not configured** — set both `ALPACA_API_KEY_ID` and "
                    "`ALPACA_API_SECRET_KEY` in your environment / Streamlit "
                    "Cloud Secrets to enable live streaming. Legacy names "
                    "`ALPACA_API_KEY` / `ALPACA_API_SECRET` are also accepted "
                    "for backward compatibility."
                )


    # ═══════════════════════════════════════════════════════════════════════════
    # Sprint 3: Client data source — pluggable adapter status panel
    # ═══════════════════════════════════════════════════════════════════════════

    with card("Client data source"):
        st.caption(level_text(
                       advisor="Where the client roster comes from. Switch via the CLIENT_ADAPTER_PROVIDER env var (or Streamlit Cloud Secret) — the active provider is the one ticked below.",
                       client="The connection to your client database.",
                   ))
        active = get_active_adapter()
        st.markdown(
            f"**Active provider:** `{active.provider_name()}` "
            f"({len(get_active_clients())} clients loaded)"
        )
        # Status row per registered adapter.
        st.markdown(
            '<div style="font-size:11px;color:var(--text-muted);'
            'text-transform:uppercase;letter-spacing:0.06em;margin:8px 0 4px;">'
            'Registered adapters</div>',
            unsafe_allow_html=True,
        )
        for name in list_registered_providers():
            try:
                inst = get_adapter(name)
                ok = inst.is_configured()
            except Exception as exc:
                ok = False
            badge = "✓ configured" if ok else "○ not configured"
            color = "var(--success)" if ok else "var(--text-muted)"
            is_active = (name == active.provider_name())
            active_marker = " · ACTIVE" if is_active else ""
            st.markdown(
                f'<div style="font-family:var(--font-mono);font-size:12px;'
                f'padding:4px 0;color:{color};">'
                f'{name:<16}  {badge}{active_marker}'
                f'</div>',
                unsafe_allow_html=True,
            )
        with st.expander("How to switch providers"):
            st.markdown("""
**Set `CLIENT_ADAPTER_PROVIDER`** to one of: `demo`, `csv_import`,
`wealthbox`, `redtail`, `salesforce_fsc`. If unset, defaults to
`demo`. If the requested provider isn't configured (no API key),
the app falls back to `demo` automatically — the demo deploy
never breaks.

**Required env vars / secrets per provider:**

- `csv_import` — places `data/clients_import.csv` (gitignored).
  Optional `CLIENT_CSV_PATH` for a custom location.
- `wealthbox` — `WEALTHBOX_API_KEY`.
- `redtail` — `REDTAIL_API_KEY` *or* the
  (`REDTAIL_USERKEY` + `REDTAIL_USERNAME` + `REDTAIL_PASSWORD`) triple.
- `salesforce_fsc` — `SALESFORCE_FSC_INSTANCE_URL` *and*
  `SALESFORCE_FSC_ACCESS_TOKEN`.

For local dev, place values in `.env`. For production deploy, set
them via Streamlit Cloud → Settings → Secrets. The app reads either
location at startup; no code change needed to switch.
            """)


    # ═══════════════════════════════════════════════════════════════════════════
    # Per-client auto-execute permissions
    # ═══════════════════════════════════════════════════════════════════════════

    with card("Per-client auto-execute permissions"):
        st.caption(level_text(
                       advisor="Per-client discretionary flag. Only honored when BROKER_PROVIDER is non-mock.",
                       client="Turn on auto-execute to let the app rebalance a client's basket on its own within their risk limits.",
                   ))
        for c in get_active_clients():
            key = f"auto_exec_{c['id']}"
            st.checkbox(
                f"{c['name']} — auto-execute rebalances",
                value=False,
                key=key,
                help=f"Tier: {c['assigned_tier']}",
            )


    # ═══════════════════════════════════════════════════════════════════════════
    # Extended modules toggle (demo-session override)
    # ═══════════════════════════════════════════════════════════════════════════

    with card("Extended modules (Framing A / B demo toggle)"):
        st.caption(
            "Toggle on to preview the RWA + DeFi tabs Day-4 will wire up. "
            "Session-only override — doesn't change config.py."
        )
        toggled = st.toggle(
            "Extended modules enabled",
            value=st.session_state.get("extended_modules_override", EXTENDED_MODULES_ENABLED),
            key="extended_modules_toggle",
        )
        st.session_state["extended_modules_override"] = toggled


    # Compliance filter — partner + Claude Design feedback 2026-04-22.
    # Default ON. Blocks leveraged crypto ETFs + single-stock covered-call
    # wrappers (MSTY/CONY/MSFO/COII/MARO) from tier allocations — many RIA
    # compliance departments prohibit these product classes with retail
    # clients. FA turns OFF explicitly when advising a client (family
    # office, qualified-purchaser, or IPS-specified) who can hold them.
    with card("Fiduciary-appropriate instrument filter"):
        st.caption(level_text(
                       advisor=(
                "core.risk_tiers.COMPLIANCE_RESTRICTED_CATEGORIES + "
                "COMPLIANCE_RESTRICTED_TICKERS. Threaded through "
                "build_portfolio(compliance_filter_on=) and "
                "_select_etfs_for_category(compliance_filter_on=)."
            ),
                       client=(
                "When ON (default), the platform hides leveraged crypto ETFs "
                "and single-stock income wrappers (MSTY, CONY, MSFO) from "
                "tier allocations. Most RIA compliance departments prohibit "
                "these with retail clients. Turn OFF only when advising a "
                "client whose IPS explicitly permits aggressive product classes."
            ),
                   ))
        compliance_on = st.toggle(
            "Restrict to fiduciary-appropriate instruments",
            value=st.session_state.get("compliance_filter_on", True),
            key="compliance_filter_toggle",
            help="Default ON. Hides leveraged + single-stock covered-call "
                 "wrappers from tier allocations. Turn off when advising "
                 "a client whose IPS explicitly permits them.",
        )
        st.session_state["compliance_filter_on"] = compliance_on


    # ═══════════════════════════════════════════════════════════════════════════
    # Diagnostics — operator-facing runtime state
    # ═══════════════════════════════════════════════════════════════════════════
    st.markdown("## Diagnostics")

    with card("EDGAR scanner health"):
        health = get_scanner_health()
        if health["last_success_ts"] is None:
            st.warning(
                "Scanner has never run in this environment. "
                "Production cron runs daily at 17:00 UTC via GitHub Actions "
                "(.github/workflows/daily_scanner.yml). Use the button below "
                "to run it on demand."
            )
        else:
            age = health["age_hours"]
            if health["is_stale"]:
                st.warning(
                    f"⚠ Last successful scan: {age:.1f} hours ago "
                    f"(threshold: {SCANNER_STALE_HOURS}h). Investigate if this persists."
                )
            else:
                st.success(
                    f"Last successful scan: {age:.1f} hours ago · "
                    f"{health['n_matches']} filings matched."
                )
            if health["keywords_queried"]:
                with st.expander("Last-run details"):
                    st.code({
                        "last_success_iso":  health["last_success_iso"],
                        "n_matches":         health["n_matches"],
                        "keywords_queried":  health["keywords_queried"],
                        "forms_queried":     health["forms_queried"],
                        "age_hours":         age,
                        "is_stale":          health["is_stale"],
                    }, language=None)

        if EDGAR_CONTACT_EMAIL.startswith("REPLACE_BEFORE_DEPLOY"):
            st.info(
                "EDGAR_CONTACT_EMAIL is still the placeholder — scanner will "
                "refuse to run. Set EDGAR_CONTACT_EMAIL in .env locally or "
                "in Streamlit Cloud Secrets + GitHub Actions Secrets for prod."
            )
        elif st.button("Run scanner now", width="content"):
            with st.spinner("Querying EDGAR full-text index…"):
                try:
                    from core.etf_universe import daily_scanner
                    matches = daily_scanner(days_back=3)
                    st.success(f"Scan complete — {len(matches)} unique filings matched.")
                except RuntimeError as exc:
                    st.error(f"Scanner refused to run: {exc}")
                except Exception as exc:
                    st.warning(f"Scanner errored (will retry on next cron): {exc}")


    # ═══════════════════════════════════════════════════════════════════════════
    # 2026-04-26 Bucket 3: New ETFs pending review (Advisor mode only)
    # ═══════════════════════════════════════════════════════════════════════════
    # When daily_scanner finds a new EDGAR filing, etf_review_queue enriches
    # it (suggested category / underlying / ticker) and adds to the pending
    # list. This panel lets the FA approve or reject each candidate. Approved
    # entries flow into the universe via data/etf_user_additions.json on the
    # next universe refresh — no config.py edit needed.

    from ui.level_helpers import is_advisor

    if is_advisor():
        with card("New ETFs pending review"):
            try:
                from core.etf_review_queue import (
                    load_queue, approve_entry, reject_entry,
                )
                _q = load_queue()
                _pending = _q.get("pending", [])
                _approved_count = len(_q.get("approved", []))
                _rejected_count = len(_q.get("rejected", []))

                st.caption(
                    f"Pending {len(_pending)} · approved {_approved_count} · "
                    f"rejected {_rejected_count}. "
                    "**As of 2026-04-27 the daily 9 AM EST scanner "
                    "auto-decides** — high-confidence crypto matches "
                    "(ticker + category + underlying all classified) go "
                    "straight to approved AND into the live universe; "
                    "off-topic filings auto-reject. After each scan the "
                    "system also recalculates every demo client's portfolio "
                    "so fresh allocations are visible immediately. "
                    "Only ambiguous filings (rare) land here for FA review."
                )

                if not _pending:
                    st.info(
                        "No filings pending review. The daily EDGAR cron will "
                        "populate this queue automatically; the manual button "
                        "above triggers an on-demand scan."
                    )
                else:
                    _CATS = [
                        "btc_spot", "eth_spot", "altcoin_spot",
                        "btc_futures", "eth_futures", "leveraged",
                        "income_covered_call", "thematic_equity",
                        "multi_asset", "defined_outcome",
                    ]
                    _UNDS = ["BTC", "ETH", "SOL", "XRP", "LTC", "DOGE",
                             "ADA", "AVAX", "HBAR", "DOT", "LINK", "MIXED"]

                    for _entry in _pending[:10]:  # cap per render
                        _acc = _entry.get("accession_number", "")
                        if not _acc:
                            continue
                        _exp = st.expander(
                            f"📄 {_entry.get('filer_name', 'unknown filer')} · "
                            f"{_entry.get('form_type', '?')} · "
                            f"{_entry.get('filing_date', '?')}",
                            expanded=False,
                        )
                        with _exp:
                            st.caption(
                                f"Accession `{_acc}` · CIK "
                                f"`{_entry.get('filer_cik', '?')}` · matched "
                                f"keywords: {', '.join(_entry.get('matched_keywords', []) or ['—'])}"
                            )
                            _form_cols = st.columns(3)
                            with _form_cols[0]:
                                _ticker = st.text_input(
                                    "Ticker",
                                    value=_entry.get("suggested_ticker") or "",
                                    key=f"rq_ticker_{_acc}",
                                    help="2-5 capital letters; auto-suggested from filing.",
                                )
                            with _form_cols[1]:
                                _cat_idx = (
                                    _CATS.index(_entry.get("suggested_category"))
                                    if _entry.get("suggested_category") in _CATS
                                    else 0
                                )
                                _category = st.selectbox(
                                    "Category", _CATS,
                                    index=_cat_idx,
                                    key=f"rq_cat_{_acc}",
                                )
                            with _form_cols[2]:
                                _und_idx = (
                                    _UNDS.index(_entry.get("suggested_underlying"))
                                    if _entry.get("suggested_underlying") in _UNDS
                                    else 0
                                )
                                _underlying = st.selectbox(
                                    "Underlying", _UNDS,
                                    index=_und_idx,
                                    key=f"rq_und_{_acc}",
                                )
                            _notes = st.text_input(
                                "Review notes (optional)",
                                key=f"rq_notes_{_acc}",
                                help="Free-form. Stored on the queue entry for audit.",
                            )
                            # Audit-fix (2026-04-30): on_click callback pattern
                            # so the rerun preserves the page route on
                            # Streamlit Cloud's multipage app. Closures
                            # capture the per-iteration values cleanly.
                            def _make_approve(_acc_=_acc, _ticker_=_ticker,
                                              _category_=_category,
                                              _underlying_=_underlying,
                                              _notes_=_notes):
                                def _do_approve() -> None:
                                    approve_entry(
                                        _acc_,
                                        ticker_override=_ticker_.strip().upper() or None,
                                        category_override=_category_,
                                        underlying_override=_underlying_,
                                        notes=_notes_,
                                    )
                                    try:
                                        st.toast(
                                            f"Approved {_ticker_ or 'filing'} — "
                                            f"will appear in universe after refresh."
                                        )
                                    except Exception:
                                        pass
                                return _do_approve
                            def _make_reject(_acc_=_acc, _notes_=_notes):
                                def _do_reject() -> None:
                                    reject_entry(_acc_, notes=_notes_)
                                    try:
                                        st.toast("Rejected — won't be re-flagged.")
                                    except Exception:
                                        pass
                                return _do_reject
                            _btn_cols = st.columns([1, 1, 4])
                            with _btn_cols[0]:
                                st.button(
                                    "✓ Approve",
                                    key=f"rq_approve_{_acc}",
                                    type="primary",
                                    use_container_width=True,
                                    on_click=_make_approve(),
                                )
                            with _btn_cols[1]:
                                st.button(
                                    "✗ Reject",
                                    key=f"rq_reject_{_acc}",
                                    use_container_width=True,
                                    on_click=_make_reject(),
                                )

                    if len(_pending) > 10:
                        st.caption(
                            f"Showing first 10 of {len(_pending)} pending. "
                            "Approve/reject the visible batch first; remaining "
                            "candidates surface on next render."
                        )
            except Exception as _rq_exc:
                # Audit-fix (HIGH): per CLAUDE.md §8 never expose Python
                # exception detail directly. Plain-English summary first,
                # diagnostic detail behind an "Advanced diagnostics"
                # expander for operator debugging. Mirrors the streaming-
                # import pattern earlier on this page.
                st.warning(
                    "Review queue is temporarily unavailable. Scanner "
                    "findings still log to disk and the approval workflow "
                    "will resume on next deploy."
                )
                with st.expander("Advanced diagnostics — review queue failure"):
                    st.code(f"{type(_rq_exc).__name__}: {_rq_exc}", language="text")


    # ═══════════════════════════════════════════════════════════════════════════
    # Circuit breaker + data-source state
    # ═══════════════════════════════════════════════════════════════════════════

    with card("Data-source state"):
        cb = circuit_breaker_state()
        s1, s2, s3 = st.columns(3)
        with s1:
            st.metric("yfinance CB", cb["active_source"])
        with s2:
            st.metric("Failures in window", cb["failure_count"])
        with s3:
            st.metric("New-ETF misses", cb["new_etf_misses"])
        if st.button("Reset circuit breaker", width="content"):
            reset_circuit_breaker()
            st.toast("Circuit breaker reset to primary source.")

        st.caption("Per-category data-source state (from this session):")
        snap = dss_snapshot()
        if snap:
            st.dataframe([
                {"Category": cat, **info} for cat, info in snap.items()
            ], width="stretch", hide_index=True)
        else:
            st.caption("No categories touched yet this session.")


    # ═══════════════════════════════════════════════════════════════════════════
    # Recent actions (audit log)
    # ═══════════════════════════════════════════════════════════════════════════

    with card("Recent actions"):
        st.caption(level_text(
                       advisor="Ring-buffered at 200 entries, oldest-first trim, atomic writes.",
                       client="Everything the advisor or the app has done on your clients' accounts recently.",
                   ))
        entries = recent_entries(limit=50)
        if entries:
            import pandas as _pd
            df = _pd.DataFrame([
                {
                    "When":    e.get("iso", "")[:19].replace("T", " "),
                    "User":    e.get("user", ""),
                    "Client":  e.get("client", ""),
                    "Action":  e.get("action", ""),
                    "Detail":  e.get("detail", ""),
                }
                for e in entries
            ])
            st.dataframe(df, width="stretch", hide_index=True)
        else:
            st.caption("No actions recorded yet this session.")


    # ═══════════════════════════════════════════════════════════════════════════
    # Runtime flag readback
    # ═══════════════════════════════════════════════════════════════════════════

    with card("Runtime flags (read-only — change in config.py)"):
        st.write({
            "EXTENDED_MODULES_ENABLED": EXTENDED_MODULES_ENABLED,
            "DEMO_MODE":                DEMO_MODE,
            "BROKER_PROVIDER":          BROKER_PROVIDER,
            "ETF_PRICE_SOURCE":         ETF_PRICE_SOURCE,
            "ETF_REFERENCE_SOURCE":     ETF_REFERENCE_SOURCE,
            "EDGAR_CONTACT_EMAIL":      (
                "<placeholder — set in .env>"
                if EDGAR_CONTACT_EMAIL.startswith("REPLACE_BEFORE_DEPLOY")
                else "configured"
            ),
        })


if __name__ == "__main__":
    main()
