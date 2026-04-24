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
        if st.button("Retry live fetch", key=banner_key, width="stretch"):
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
                # Keep this column as a uniform numeric type so pyarrow
                # can serialize. Mixing int + "—" string previously
                # raised pyarrow.lib.ArrowInvalid on Streamlit Cloud.
                # Using pandas nullable Int64 (capital I) so missing
                # values render as <NA> rather than poisoning the dtype.
                "Age (min)":      age,
                "Affected metrics": ", ".join(metrics_list) if metrics_list else "—",
            })
        df = _pd.DataFrame(rows)
        # Cast Age column to nullable Int64 so missing values become
        # <NA> (rendered as blank by Streamlit) instead of NaN floats.
        if "Age (min)" in df.columns:
            df["Age (min)"] = df["Age (min)"].astype("Int64")
        st.dataframe(
            df,
            width="stretch",
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


# ── DV-2 compliance helper (CLAUDE.md §22 item 5) ───────────────────────
#
# Every performance display must include: 1Y / 3Y / 5Y / since-inception
# returns, benchmark comparison, max drawdown, "Hypothetical results"
# disclaimer, and methodology link. This helper renders the first four.
# Disclaimer and methodology link remain the caller's responsibility.
#
# Added 2026-04-23 for DV-2. See shared-docs/deployment-checklists/
# etf-advisor-platform.md item C1 and pending_work.md DV-2 for context.

def _ps_simple_return_pct(closes: list[float], n_days: int) -> float | None:
    if len(closes) <= n_days:
        return None
    start = closes[-n_days - 1] if n_days > 0 else closes[0]
    end = closes[-1]
    if start <= 0:
        return None
    return ((end / start) - 1.0) * 100.0


def _ps_cagr_pct(closes: list[float], n_calendar_days: int) -> float | None:
    """Annualized return from first to last close. n_calendar_days used to
    derive the elapsed time; if ambiguous, falls back to len(closes) * 365/252."""
    if len(closes) < 30:
        return None
    start = closes[0]
    end = closes[-1]
    if start <= 0 or end <= 0:
        return None
    years = n_calendar_days / 365.25 if n_calendar_days >= 30 else (len(closes) * 365.25 / 252) / 365.25
    if years < (30 / 365.25):
        return None
    try:
        ratio = end / start
        if ratio <= 0:
            return None
        return ((ratio ** (1.0 / years)) - 1.0) * 100.0
    except (ValueError, ZeroDivisionError, OverflowError):
        return None


def _ps_max_drawdown_pct(closes: list[float]) -> float | None:
    """Peak-to-trough drawdown over the series. Returns a non-positive %, or None."""
    if not closes:
        return None
    peak = closes[0]
    max_dd = 0.0
    for c in closes:
        if c > peak:
            peak = c
        if peak > 0:
            dd = (c / peak) - 1.0
            if dd < max_dd:
                max_dd = dd
    return max_dd * 100.0


def _ps_fmt_pct(val: float | None, fallback_years_needed: int | None = None) -> str:
    if val is None:
        if fallback_years_needed:
            return f"N/A (<{fallback_years_needed}Y hist)"
        return "—"
    return f"{val:+.2f}%"


def _ps_fmt_dd(val: float | None) -> str:
    if val is None:
        return "—"
    return f"{val:+.2f}%"


def _ps_row(ticker: str, source: str, price_rows: list[dict]) -> dict:
    """Build one display row from a (source, price_rows) pair. Robust to None / empty."""
    import pandas as pd

    if not price_rows:
        return {
            "ticker":            ticker,
            "source":            source or "unavailable",
            "inception":         "—",
            "1Y %":              _ps_fmt_pct(None, fallback_years_needed=1),
            "3Y %":              _ps_fmt_pct(None, fallback_years_needed=3),
            "5Y %":              _ps_fmt_pct(None, fallback_years_needed=5),
            "since-inception %": "—",
            "max drawdown %":    "—",
        }

    df = pd.DataFrame(price_rows)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    closes = [float(c) for c in df["close"].astype(float).tolist() if c and c > 0]
    if not closes:
        return _ps_row(ticker, source, [])

    inception_date = df["date"].iloc[0].strftime("%Y-%m-%d")
    n_calendar_days = int((df["date"].iloc[-1] - df["date"].iloc[0]).days)

    return {
        "ticker":            ticker,
        "source":            source or "unavailable",
        "inception":         inception_date,
        "1Y %":              _ps_fmt_pct(_ps_simple_return_pct(closes, 252), fallback_years_needed=1),
        "3Y %":              _ps_fmt_pct(_ps_simple_return_pct(closes, 252 * 3), fallback_years_needed=3),
        "5Y %":              _ps_fmt_pct(_ps_simple_return_pct(closes, 252 * 5), fallback_years_needed=5),
        "since-inception %": _ps_fmt_pct(_ps_cagr_pct(closes, n_calendar_days)),
        "max drawdown %":    _ps_fmt_dd(_ps_max_drawdown_pct(closes)),
    }


def _ps_blended_benchmark_row(
    benchmark_weights: dict[str, float],
    benchmark_price_data: dict[str, dict],
    label: str,
) -> dict:
    """
    Synthetic blended benchmark row. Component returns weighted by the
    provided weights dict. Uses static weights (no daily rebalancing) —
    close enough for advisor-facing display per §22 item 5; exact
    rebalancing model documented on the Methodology page.
    """
    import pandas as pd

    # Compute each component's series + returns. Skip missing components;
    # normalize remaining weights so the row still renders something useful.
    component_series: dict[str, tuple[list[float], int]] = {}
    usable_weights: dict[str, float] = {}
    for ticker, weight in benchmark_weights.items():
        entry = benchmark_price_data.get(ticker, {}) or {}
        rows = entry.get("prices", []) or []
        if not rows:
            continue
        df = pd.DataFrame(rows)
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").reset_index(drop=True)
        closes = [float(c) for c in df["close"].astype(float).tolist() if c and c > 0]
        if not closes:
            continue
        n_days = int((df["date"].iloc[-1] - df["date"].iloc[0]).days)
        component_series[ticker] = (closes, n_days)
        usable_weights[ticker] = weight

    if not component_series:
        return {
            "ticker":            f"Benchmark ({label})",
            "source":            "unavailable",
            "inception":         "—",
            "1Y %":              "—",
            "3Y %":              "—",
            "5Y %":              "—",
            "since-inception %": "—",
            "max drawdown %":    "—",
        }

    total_w = sum(usable_weights.values())
    norm_w = {t: w / total_w for t, w in usable_weights.items()}

    def _weighted(method_horizon: int | None, is_cagr: bool = False, is_dd: bool = False) -> float | None:
        acc = 0.0
        total = 0.0
        for ticker, (closes, n_days) in component_series.items():
            if is_dd:
                val = _ps_max_drawdown_pct(closes)
            elif is_cagr:
                val = _ps_cagr_pct(closes, n_days)
            else:
                val = _ps_simple_return_pct(closes, method_horizon or 0)
            if val is None:
                continue
            acc += norm_w[ticker] * val
            total += norm_w[ticker]
        return (acc / total) if total > 0 else None

    return {
        "ticker":            f"Benchmark ({label})",
        "source":            "blended",
        "inception":         "—",
        "1Y %":              _ps_fmt_pct(_weighted(252), fallback_years_needed=1),
        "3Y %":              _ps_fmt_pct(_weighted(252 * 3), fallback_years_needed=3),
        "5Y %":              _ps_fmt_pct(_weighted(252 * 5), fallback_years_needed=5),
        "since-inception %": _ps_fmt_pct(_weighted(None, is_cagr=True)),
        "max drawdown %":    _ps_fmt_dd(_weighted(None, is_dd=True)),
    }


def performance_summary_table(
    tickers: list[str],
    price_data: dict[str, dict],
    benchmark_weights: dict[str, float] | None = None,
    benchmark_label: str | None = None,
    benchmark_price_data: dict[str, dict] | None = None,
):
    """
    Compliance-complete performance summary per CLAUDE.md §22 item 5.

    Returns a pandas DataFrame with columns:
      ticker · source · inception · 1Y % · 3Y % · 5Y % · since-inception % · max drawdown %

    Cells are pre-formatted strings. Returns too short show
    "N/A (<N>Y hist)" instead of blank/None so FAs can see *why* a cell
    is empty (fund too new) vs. a data-fetch failure.

    If benchmark_weights + benchmark_price_data are both provided, appends
    one blended benchmark row at the bottom labeled
    "Benchmark (<benchmark_label>)". Benchmark uses static weights (no
    daily rebalancing); documented limitation per Methodology page.

    Parameters
    ----------
    tickers : list[str]
        ETF tickers to include, in display order.
    price_data : dict[str, dict]
        Output of integrations.data_feeds.get_etf_prices(tickers, period="5y").
    benchmark_weights : dict[str, float] | None
        e.g. config.BENCHMARK_DEFAULT. Component ticker → weight (sums to 1.0).
    benchmark_label : str | None
        Human-readable label, e.g. config.BENCHMARK_LABEL.
    benchmark_price_data : dict[str, dict] | None
        Price-data bundle for the benchmark components (fetched separately
        via get_etf_prices(list(benchmark_weights.keys()), period="5y")).

    Returns
    -------
    pandas.DataFrame
    """
    import pandas as pd

    rows: list[dict] = []
    for ticker in tickers:
        entry = price_data.get(ticker, {}) or {}
        rows.append(_ps_row(
            ticker=ticker,
            source=entry.get("source", "unavailable"),
            price_rows=entry.get("prices", []) or [],
        ))

    if benchmark_weights and benchmark_price_data and benchmark_label:
        rows.append(_ps_blended_benchmark_row(
            benchmark_weights=benchmark_weights,
            benchmark_price_data=benchmark_price_data,
            label=benchmark_label,
        ))

    return pd.DataFrame(rows)
