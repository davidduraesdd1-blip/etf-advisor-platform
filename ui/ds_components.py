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
        f'<div class="ds-rail-brand">'
        f'<div class="ds-brand-dot" style="background:{accent["accent"]};color:{accent["accent_ink"]};">{brand_glyph}</div>'
        f'<div>'
        f'<div style="font-family:var(--font-display);font-size:17px;font-weight:500;color:var(--text-primary);letter-spacing:-0.015em;">{brand_name}</div>'
        f'<div style="font-size:11px;color:var(--text-muted);letter-spacing:0.04em;">{brand_sub}</div>'
        f'</div>'
        f'</div>',
        unsafe_allow_html=True,
    )


def render_top_bar(
    *,
    breadcrumb: Sequence[str] = ("Dashboard",),
    user_level: Literal["Advisor", "Client"] = "Advisor",
    show_level: bool = True,
    show_refresh: bool = True,
    show_theme: bool = True,
) -> None:
    """Render the topbar: breadcrumb on the left, real Streamlit widget
    controls on the right (Advisor/Client pills + Refresh + Theme).

    2026-04-26 hotfix: the previous implementation rendered the entire
    topbar as a single block of HTML with `<button>` elements. Those
    are decorative — Streamlit can't capture clicks on raw HTML. This
    rewrite splits the layout into st.columns and replaces the chips
    with real st.button widgets that update session_state and rerun.

    Wired actions:
      Advisor / Client pills  → st.session_state["user_level"] = "..."
      ↻ Refresh               → st.cache_data.clear(); st.rerun()
      ☾ Theme                 → flip st.session_state["theme"]; st.rerun()

    Active mode pill uses type="primary" so it inherits the advisor
    teal accent from ui/overrides.py + .streamlit/config.toml.
    """
    if st is None:
        return

    # Normalize level — accept old lowercase placeholders for back-compat.
    active = (user_level or "").strip().capitalize()
    if active not in ("Advisor", "Client"):
        active = "Advisor"

    # Layout: breadcrumb (wide) + 2 mode pills + refresh + theme.
    # Column widths picked so the chips on the right read as a tight cluster
    # and the breadcrumb gets all the leftover space.
    crumb_col, advisor_col, client_col, refresh_col, theme_col = st.columns(
        [6, 1, 1, 1, 1], gap="small",
    )

    *rest, last = list(breadcrumb) or ["", ""]
    crumb_html = " / ".join(rest) + (" / " if rest else "") + f"<b>{last}</b>"
    with crumb_col:
        st.markdown(
            f'<div class="ds-crumbs" style="padding-top:6px;color:var(--text-muted);'
            f'font-size:13px;">{crumb_html}</div>',
            unsafe_allow_html=True,
        )

    if show_level:
        with advisor_col:
            if st.button(
                "Advisor",
                key="topbar_mode_advisor",
                use_container_width=True,
                type=("primary" if active == "Advisor" else "secondary"),
            ):
                st.session_state["user_level"] = "Advisor"
                st.rerun()
        with client_col:
            if st.button(
                "Client",
                key="topbar_mode_client",
                use_container_width=True,
                type=("primary" if active == "Client" else "secondary"),
            ):
                st.session_state["user_level"] = "Client"
                st.rerun()

    if show_refresh:
        with refresh_col:
            if st.button(
                "↻ Refresh",
                key="topbar_refresh",
                use_container_width=True,
                help="Clear all data caches and reload the page.",
            ):
                # Clear both cache layers + reset the data-source state
                # registry so the next page render sees fresh fetches and
                # the data-source badges flip back to LIVE on success.
                try:
                    st.cache_data.clear()
                except Exception:
                    pass
                try:
                    st.cache_resource.clear()
                except Exception:
                    pass
                try:
                    from integrations.data_feeds import reset_circuit_breaker
                    reset_circuit_breaker()
                except Exception:
                    pass
                # Toast survives the rerun and gives visible confirmation
                # — without this the user can't tell the click fired.
                try:
                    st.toast("Caches cleared — refetching live data", icon="✓")
                except Exception:
                    pass
                st.rerun()

    if show_theme:
        with theme_col:
            cur_theme = st.session_state.get("theme", "dark")
            label = "☼ Light" if cur_theme == "dark" else "☾ Dark"
            if st.button(
                label,
                key="topbar_theme",
                use_container_width=True,
                help="Toggle between dark and light mode.",
            ):
                st.session_state["theme"] = (
                    "light" if cur_theme == "dark" else "dark"
                )
                st.rerun()


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
        f'<div class="ds-page-hd">'
        f'<div>'
        f'<h1 class="ds-page-title">{title}</h1>'
        f'{sub_html}'
        f'</div>'
        f'{pills_html}'
        f'</div>',
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
