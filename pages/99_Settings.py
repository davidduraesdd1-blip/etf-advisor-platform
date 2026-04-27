"""
Settings — feature flags readback, broker routing, monitoring preferences,
per-client auto-execute toggles, scanner health indicator.

Scanner health comes from core.etf_universe.get_scanner_health() which
reads data/scanner_health.json (written atomically on every successful
daily_scanner run, per Day-3 item B).
"""
from __future__ import annotations

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
from core.demo_clients import DEMO_CLIENTS
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
    # Per-client auto-execute permissions
    # ═══════════════════════════════════════════════════════════════════════════

    with card("Per-client auto-execute permissions"):
        st.caption(level_text(
                       advisor="Per-client discretionary flag. Only honored when BROKER_PROVIDER is non-mock.",
                       client="Turn on auto-execute to let the app rebalance a client's basket on its own within their risk limits.",
                   ))
        for c in DEMO_CLIENTS:
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
                    f"rejected {_rejected_count}. Run "
                    "**Run scanner now** above to refresh. Each candidate is "
                    "auto-enriched with a suggested category/underlying/ticker "
                    "from the SEC filing — review and approve to flow into the "
                    "live universe (no config.py edit needed)."
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
                            _btn_cols = st.columns([1, 1, 4])
                            with _btn_cols[0]:
                                if st.button(
                                    "✓ Approve",
                                    key=f"rq_approve_{_acc}",
                                    type="primary",
                                    use_container_width=True,
                                ):
                                    approve_entry(
                                        _acc,
                                        ticker_override=_ticker.strip().upper() or None,
                                        category_override=_category,
                                        underlying_override=_underlying,
                                        notes=_notes,
                                    )
                                    st.toast(
                                        f"Approved {_ticker or 'filing'} — "
                                        f"will appear in universe after refresh."
                                    )
                                    st.rerun()
                            with _btn_cols[1]:
                                if st.button(
                                    "✗ Reject",
                                    key=f"rq_reject_{_acc}",
                                    use_container_width=True,
                                ):
                                    reject_entry(_acc, notes=_notes)
                                    st.toast(f"Rejected — won't be re-flagged.")
                                    st.rerun()

                    if len(_pending) > 10:
                        st.caption(
                            f"Showing first 10 of {len(_pending)} pending. "
                            "Approve/reject the visible batch first; remaining "
                            "candidates surface on next render."
                        )
            except Exception as _rq_exc:
                st.warning(
                    f"Review queue unavailable ({type(_rq_exc).__name__}: {_rq_exc}). "
                    "Scanner findings will still log; approval workflow is offline."
                )


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
