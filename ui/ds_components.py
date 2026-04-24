"""
ui/ds_components.py — Advisor-family design-system components.

These helpers render the top bar, page header, and 4-col KPI strip used on
every advisor mockup in shared-docs/design-mockups/advisor-etf-*.html.

They consume the CSS tokens injected by ui/design_system.py::inject_theme
and the widget overrides from ui/overrides.py.
"""
from __future__ import annotations

from typing import Iterable, Literal, Sequence

try:
    import streamlit as st
except ImportError:
    st = None  # type: ignore


def render_sidebar_brand(
    *,
    brand_name: str = "Advisor",
    brand_sub: str = "family office",
    brand_glyph: str = "A",
) -> None:
    """Render the advisor brand block at the top of the Streamlit sidebar."""
    if st is None:
        return
    try:
        from .design_system import ACCENTS
        accent = ACCENTS["etf-advisor-platform"]
    except Exception:
        accent = {"accent": "#0fa68a", "accent_ink": "#0c0d12"}
    st.sidebar.markdown(
        f"""
        <div class="ds-rail-brand">
          <div class="ds-brand-dot" style="background:{accent['accent']};color:{accent['accent_ink']};">{brand_glyph}</div>
          <div>
            <div style="font-family:var(--font-display);font-size:17px;font-weight:500;color:var(--text-primary);letter-spacing:-0.015em;">{brand_name}</div>
            <div style="font-size:11px;color:var(--text-muted);letter-spacing:0.04em;">{brand_sub}</div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_top_bar(
    *,
    breadcrumb: Sequence[str] = ("Dashboard",),
    user_level: Literal["beginner", "intermediate", "advanced"] = "beginner",
    show_level: bool = True,
    show_refresh: bool = True,
    show_theme: bool = True,
) -> None:
    if st is None:
        return
    *rest, last = list(breadcrumb) or ["", ""]
    crumb_html = " / ".join(rest) + (" / " if rest else "") + f"<b>{last}</b>"
    level_html = ""
    if show_level:
        lvls = [("beginner", "Beginner"), ("intermediate", "Intermediate"), ("advanced", "Advanced")]
        buttons = "".join(
            f'<button class="{"on" if user_level == k else ""}" data-level="{k}">{lbl}</button>'
            for k, lbl in lvls
        )
        level_html = f'<div class="ds-level-group">{buttons}</div>'
    refresh_html = '<button class="ds-chip-btn" data-action="refresh">↻ Refresh</button>' if show_refresh else ""
    theme_html   = '<button class="ds-chip-btn" data-action="theme">☾ Theme</button>' if show_theme else ""
    st.markdown(
        f"""
        <div class="ds-topbar">
          <div class="ds-crumbs">{crumb_html}</div>
          <div class="ds-topbar-spacer"></div>
          {level_html}
          {refresh_html}
          {theme_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


def page_header(
    title: str,
    subtitle: str = "",
    *,
    data_sources: Iterable[tuple[str, str]] | None = None,
) -> None:
    if st is None:
        return
    if data_sources:
        pills = []
        for label, status in data_sources:
            cls = "ds-pill"
            if status == "cached":
                cls += " warn"
            elif status == "down":
                cls += " down"
            pills.append(f'<span class="{cls}"><span class="tick"></span> {label} · {status}</span>')
        pills_html = f'<div class="ds-row">{"".join(pills)}</div>'
    else:
        pills_html = ""
    sub_html = f'<div class="ds-page-sub">{subtitle}</div>' if subtitle else ""
    st.markdown(
        f"""
        <div class="ds-page-hd">
          <div>
            <h1 class="ds-page-title">{title}</h1>
            {sub_html}
          </div>
          {pills_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


def kpi_strip(items: Sequence[tuple[str, str, str]]) -> None:
    """4-col KPI strip used at the top of advisor mockups."""
    if st is None:
        return
    cells = [
        f'<div><div class="lbl">{l}</div><div class="val">{v}</div><div class="sub">{s}</div></div>'
        for l, v, s in items
    ]
    st.markdown(
        f'<div class="ds-card ds-strip">{"".join(cells)}</div>',
        unsafe_allow_html=True,
    )
