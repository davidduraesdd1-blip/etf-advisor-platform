"""
Shared UI primitives. Every page composes from these so visual rhythm stays
consistent.

CLAUDE.md governance: Section 8 (cards, signal badges, tap targets).
"""
from __future__ import annotations

from typing import Literal

import streamlit as st


def section_header(title: str, subtitle: str | None = None) -> None:
    """Left-accent-stripe header used on every page section."""
    st.markdown(f"### {title}")
    if subtitle:
        st.caption(subtitle)


def card(title: str | None = None) -> st.delta_generator.DeltaGenerator:
    """
    Open a themed card container.

        with card("Client overview"):
            st.write("...")
    """
    container = st.container(border=True)
    if title:
        container.markdown(f"#### {title}")
    return container


def signal_badge(signal: Literal["BUY", "HOLD", "SELL"]) -> None:
    """Shape + color-encoded signal per CLAUDE.md §8 (color-blind safe)."""
    shape = {"BUY": "▲", "HOLD": "■", "SELL": "▼"}[signal]
    cls = {"BUY": "eap-signal eap-signal-buy",
           "HOLD": "eap-signal eap-signal-hold",
           "SELL": "eap-signal eap-signal-sell"}[signal]
    st.markdown(
        f"<span class='{cls}'>{shape} {signal}</span>",
        unsafe_allow_html=True,
    )


def kpi_tile(label: str, value: str, delta: str | None = None) -> None:
    """Streamlit's metric primitive with the label visually de-emphasized."""
    st.metric(label=label, value=value, delta=delta)


def disclosure(text: str) -> None:
    """Amber-bordered disclosure banner — used for compliance text."""
    st.markdown(
        f"<div class='eap-disclosure'>{text}</div>",
        unsafe_allow_html=True,
    )


def coming_soon(page_name: str) -> None:
    """Default render for placeholder pages during Day 1 scaffold."""
    with st.container(border=True):
        st.markdown(f"### Coming soon — {page_name}")
        st.caption(
            "Scaffold in place. Wiring to data + math layers lands on Day 2 and Day 3."
        )


# ═══════════════════════════════════════════════════════════════════════════
# Fallback transparency primitive (Day-3 first-class UX requirement)
# ═══════════════════════════════════════════════════════════════════════════

_SOURCE_LABEL = {
    "yfinance":      "Yahoo Finance",
    "stooq":         "Stooq (delayed ~15min)",
    "alphavantage":  "Alpha Vantage",
    "edgar":         "SEC EDGAR",
    "fred":          "FRED",
    "cache":         "cached data",
    "static":        "static fallback",
    "none":          "no source",
}


def _affected_metrics_phrase(category: str,
                              consumer_label: str | None) -> str:
    """
    Build a user-facing description of WHICH metrics this category
    feeds. If the caller passed an explicit consumer_label (e.g.,
    "Sharpe ratio" from the Portfolio KPI row), use it verbatim. If
    not, look up METRIC_DEPENDENCIES from data_source_state and
    render the first 2-3 downstream metric names as a concise list.
    """
    if consumer_label:
        return consumer_label
    from core.data_source_state import affected_metrics
    metrics = affected_metrics(category)
    if not metrics:
        return "this panel"
    if len(metrics) == 1:
        return metrics[0]
    if len(metrics) == 2:
        return f"{metrics[0]} and {metrics[1]}"
    return f"{metrics[0]}, {metrics[1]}, and {len(metrics) - 2} other metric" + (
        "s" if len(metrics) - 2 > 1 else ""
    )


def data_source_badge(
    category: str,
    state: "str | None" = None,
    source: "str | None" = None,
    age_minutes: "int | None" = None,
    consumer_label: "str | None" = None,
) -> None:
    """
    Render the fallback-transparency badge for a data category.

    STATE 1 (LIVE)          — renders nothing.
    STATE 2 (FALLBACK_LIVE) — small amber-dot badge + source name +
                              affected-metric label.
    STATE 3 (CACHED)        — amber banner with age + affected
                              metric + Retry button.
    STATIC                  — footnote-style annotation naming the
                              specific consumer metric.

    `consumer_label` (Option 3): a short string naming the UI metric
    that consumes this category — e.g., "Sharpe ratio" or "Historical
    chart". If omitted, the badge looks up METRIC_DEPENDENCIES and
    names the affected metrics generically. Passing an explicit label
    produces the clearest message ("The Sharpe ratio is using…") when
    only one KPI nearby consumes this category.

    Normally called without arguments beyond `category` — the current
    state, source, and age are read live from core.data_source_state.
    """
    import html as _html

    from core.data_source_state import (
        DataSourceState,
        get_age_minutes,
        get_source,
        get_state,
        reset_all,
    )

    resolved_state = state if state is not None else get_state(category).value
    resolved_source = source if source is not None else get_source(category)
    resolved_age = age_minutes if age_minutes is not None else get_age_minutes(category)

    affected = _affected_metrics_phrase(category, consumer_label)
    affected_html = _html.escape(affected)

    # STATE 1 — nothing to show.
    if resolved_state in (DataSourceState.LIVE.value, DataSourceState.UNKNOWN.value):
        return

    # STATE 2 — secondary/tertiary live source active.
    if resolved_state == DataSourceState.FALLBACK_LIVE.value:
        pretty = _SOURCE_LABEL.get(resolved_source, resolved_source or "alternate")
        pretty_html = _html.escape(str(pretty))
        st.markdown(
            f"<span class='eap-dss-badge eap-dss-fallback' "
            f"title='{affected_html} — primary source unavailable; "
            f"serving from {pretty_html}.'>"
            f"● {affected_html}: source → {pretty_html}</span>",
            unsafe_allow_html=True,
        )
        return

    # STATIC — footnote-style annotation naming the specific consumer.
    if resolved_state == DataSourceState.STATIC.value:
        pretty = _SOURCE_LABEL.get(resolved_source, "static estimate")
        pretty_html = _html.escape(str(pretty))
        st.markdown(
            f"<span class='eap-dss-footnote'>"
            f"¹ {affected_html} is using {pretty_html} — primary "
            f"live source temporarily unavailable.</span>",
            unsafe_allow_html=True,
        )
        return

    # STATE 3 — cached last-known data. Prominent banner + retry.
    age_label = f"{resolved_age} min ago" if resolved_age is not None else "unknown age"
    banner_key = f"eap_dss_retry_{category}"
    col_msg, col_btn = st.columns([4, 1])
    with col_msg:
        st.markdown(
            f"<div class='eap-dss-banner'>"
            f"⚠ {affected_html} — last live update {age_label}. "
            f"Primary source temporarily unavailable."
            f"</div>",
            unsafe_allow_html=True,
        )
    with col_btn:
        if st.button("Retry live fetch", key=banner_key, use_container_width=True):
            reset_all()
            st.toast("Live fetch retry queued — refreshing caches.")


def data_sources_panel(
    categories: list[str] | None = None,
    *,
    expanded: bool = False,
    key: str = "data_sources_panel",
) -> None:
    """
    Top-of-page live status grid for every data-source category this
    app consumes. Lets the FA audit the whole data stack at a glance
    without having to read tile-level footnotes scattered across the
    page.

    Each row shows:
      - Data source (human-friendly label)
      - Current state (LIVE / FALLBACK_LIVE / CACHED / STATIC / UNKNOWN)
      - Active source (yfinance, stooq, fred, edgar, cache, static, …)
      - Age of most recent successful fetch (minutes)
      - Affected metrics (comma-separated list from METRIC_DEPENDENCIES)

    Collapsed by default so it doesn't dominate above-the-fold — the
    FA clicks to expand when they want the audit.

    `categories`: if None, shows every known category from METRIC_DEPENDENCIES.
    """
    from core.data_source_state import (
        DataSourceState,
        METRIC_DEPENDENCIES,
        get_age_minutes,
        get_source,
        get_state,
        human_category_label,
    )

    shown_cats = categories if categories is not None else list(METRIC_DEPENDENCIES.keys())

    # Summary dot — green if every category is LIVE, amber if any are
    # in a fallback state, grey if nothing's been touched this session.
    states = [get_state(c).value for c in shown_cats]
    any_fallback = any(
        s in (DataSourceState.FALLBACK_LIVE.value,
              DataSourceState.CACHED.value,
              DataSourceState.STATIC.value)
        for s in states
    )
    all_live_or_unknown = all(
        s in (DataSourceState.LIVE.value, DataSourceState.UNKNOWN.value)
        for s in states
    )
    if any_fallback:
        summary = "Data sources — some categories in fallback"
    elif all_live_or_unknown and any(s == DataSourceState.LIVE.value for s in states):
        summary = "Data sources — all live"
    else:
        summary = "Data sources — awaiting first fetch"

    with st.expander(summary, expanded=expanded):
        import pandas as _pd
        rows = []
        for cat in shown_cats:
            state = get_state(cat).value
            src = get_source(cat) or "—"
            age = get_age_minutes(cat)
            metrics_list = METRIC_DEPENDENCIES.get(cat, [])
            rows.append({
                "Data source":    human_category_label(cat),
                "State":          state,
                "Active source":  src,
                "Age (min)":      age if age is not None else "—",
                "Affected metrics": ", ".join(metrics_list) if metrics_list else "—",
            })
        df = _pd.DataFrame(rows)
        st.dataframe(
            df,
            use_container_width=True,
            hide_index=True,
            key=key,
        )
        st.caption(
            "LIVE = primary source succeeded most recently. "
            "FALLBACK_LIVE = secondary live source served the request. "
            "CACHED = all live sources failed; serving last-known data. "
            "STATIC = no cache available; serving a hardcoded default. "
            "UNKNOWN = category not yet queried this session."
        )


def tier_pill_selector(options: list[str], default_index: int = 2,
                        key: str = "tier_pill") -> str:
    """Horizontal radio styled as pills — used for the 5-tier selector."""
    return st.radio(
        "Risk tier",
        options=options,
        index=default_index,
        horizontal=True,
        key=key,
    )


def safe_page_link(page: str, label: str, icon: str | None = None) -> None:
    """
    Render an st.page_link, tolerating Streamlit AppTest's missing
    page-registry context. AppTest raises `KeyError: 'url_pathname'`
    because pages sibling to the one under test aren't registered.
    In a real browser the link renders normally.
    """
    try:
        st.page_link(page, label=label, icon=icon)
    except KeyError:
        st.caption(f"{icon or '→'} {label} — `{page}`")
    except Exception:
        st.caption(f"→ {label}")
