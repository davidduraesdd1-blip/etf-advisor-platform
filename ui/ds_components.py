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
        # Audit-fix (2026-04-30): use on_click callbacks instead of an
        # explicit st.rerun() inside the button handler. On Streamlit
        # Cloud's multipage app the explicit st.rerun() can lose the
        # page context and fall through to the default route (Home),
        # showing the user a "fallback" screen. The callback pattern
        # below sets session_state DURING button-handling and lets
        # Streamlit's natural button-click rerun complete on the
        # current page — URL preserved.
        def _set_advisor() -> None:
            st.session_state["user_level"] = "Advisor"
        def _set_client() -> None:
            st.session_state["user_level"] = "Client"
        with advisor_col:
            st.button(
                "Advisor",
                key="topbar_mode_advisor",
                use_container_width=True,
                type=("primary" if active == "Advisor" else "secondary"),
                on_click=_set_advisor,
            )
        with client_col:
            st.button(
                "Client",
                key="topbar_mode_client",
                use_container_width=True,
                type=("primary" if active == "Client" else "secondary"),
                on_click=_set_client,
            )

    if show_refresh:
        with refresh_col:
            # Audit-fix (2026-04-30): on_click callback pattern so the
            # cache-clear + rerun preserves the page route on Streamlit
            # Cloud's multipage app. Same fix as the level + theme
            # toggles. The button click itself triggers the rerun;
            # the callback sets state during click handling so the
            # rest of the page sees cleared caches + the new
            # last_refresh_ts.
            def _on_refresh() -> None:
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
                import time as _refresh_time
                st.session_state["last_refresh_ts"] = _refresh_time.time()
                try:
                    st.toast("Caches cleared — refetching live data", icon="✓")
                except Exception:
                    pass
            st.button(
                "↻ Refresh",
                key="topbar_refresh",
                use_container_width=True,
                help="Clear all data caches and reload the page.",
                on_click=_on_refresh,
            )

            # "Refreshed Xs ago" caption, only when a recent refresh fired.
            # Renders directly below the Refresh button so the feedback is
            # unmissable — the user sees the timestamp tick from "just now"
            # to "1s ago" / "2s ago" / etc. on subsequent reruns.
            _last = st.session_state.get("last_refresh_ts")
            if _last:
                import time as _t
                _delta = max(0, int(_t.time() - _last))
                if _delta < 60:
                    _ago = "just now" if _delta < 2 else f"{_delta}s ago"
                else:
                    _ago = f"{_delta // 60}m ago"
                st.markdown(
                    f'<div style="font-size:10.5px;color:var(--text-muted);'
                    f'text-align:center;margin-top:4px;font-family:var(--font-mono);">'
                    f'✓ refreshed {_ago}</div>',
                    unsafe_allow_html=True,
                )

    if show_theme:
        with theme_col:
            cur_theme = st.session_state.get("theme", "dark")
            label = "☼ Light" if cur_theme == "dark" else "☾ Dark"
            # Audit-fix (2026-04-30): same on_click pattern as the level
            # toggle so theme switches don't lose the page-route on
            # Streamlit Cloud's multipage app.
            def _toggle_theme() -> None:
                _cur = st.session_state.get("theme", "dark")
                st.session_state["theme"] = "light" if _cur == "dark" else "dark"
            st.button(
                label,
                key="topbar_theme",
                use_container_width=True,
                help="Toggle between dark and light mode.",
                on_click=_toggle_theme,
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
