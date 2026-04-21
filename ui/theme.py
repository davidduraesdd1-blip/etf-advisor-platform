"""
Theme + global CSS injection.

Call `apply_theme()` once at the top of every page. Reads the user's theme
choice from session_state ("dark" or "light"; "dark" is the default per
CLAUDE.md §8 — Beginner default = dark).

CLAUDE.md governance: Section 8 (design standards).
"""
from __future__ import annotations

import streamlit as st

from config import COLORS, FONTS, TYPE_SCALE


def current_theme() -> str:
    """Return 'dark' or 'light' from session state, defaulting to dark."""
    return st.session_state.get("theme", "dark")


def toggle_theme() -> None:
    """Flip the theme. Intended as a button on_click handler."""
    st.session_state["theme"] = "light" if current_theme() == "dark" else "dark"


def _css_for_theme(theme: str) -> str:
    if theme == "light":
        bg = COLORS["light_bg"]
        card = COLORS["light_card"]
        text = "#0f172a"
        muted = "#475569"
        border = "#e2e8f0"
        badge_success = COLORS["success_on_light"]
        badge_danger = COLORS["danger_on_light"]
        badge_warning = COLORS["warning_on_light"]
    else:
        bg = COLORS["dark_bg"]
        card = COLORS["dark_card"]
        text = "#e5e7eb"
        muted = "#9ca3af"
        border = "#1f2937"
        badge_success = COLORS["success"]
        badge_danger = COLORS["danger"]
        badge_warning = COLORS["warning"]

    return f"""
    <style>
      @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap');

      :root {{
        --primary: {COLORS["primary"]};
        --success: {COLORS["success"]};
        --danger:  {COLORS["danger"]};
        --warning: {COLORS["warning"]};
        --bg:      {bg};
        --card:    {card};
        --text:    {text};
        --muted:   {muted};
        --border:  {border};
        --badge-success: {badge_success};
        --badge-danger:  {badge_danger};
        --badge-warning: {badge_warning};
      }}

      html, body, [data-testid="stAppViewContainer"] {{
        background-color: var(--bg) !important;
        color: var(--text) !important;
        font-family: {FONTS["ui"]};
      }}

      [data-testid="stSidebar"] {{
        background-color: var(--card) !important;
        border-right: 1px solid var(--border);
      }}

      /* Typography floors — never cross these per CLAUDE.md §8 */
      body, p, div, span, label {{
        font-size: {TYPE_SCALE["body"]};
      }}
      h1, h2, h3 {{
        font-family: {FONTS["ui"]};
        font-size: {TYPE_SCALE["heading"]};
        font-weight: 600;
      }}
      .stMarkdown small, .stCaption, [data-testid="stMetricLabel"] {{
        font-size: {TYPE_SCALE["label"]};
      }}
      [data-testid="stMetricValue"] {{
        font-family: {FONTS["data"]};
        font-size: {TYPE_SCALE["kpi"]};
        font-weight: 600;
      }}

      /* Card primitive */
      .eap-card {{
        background: var(--card);
        border: 1px solid var(--border);
        border-radius: 10px;
        padding: 18px 20px;
        box-shadow: 0 1px 2px rgba(0,0,0,0.12);
        margin-bottom: 14px;
      }}
      .eap-card h3 {{
        margin-top: 0;
        border-left: 3px solid var(--primary);
        padding-left: 10px;
      }}

      /* Signal badges — shape + color (CLAUDE.md §8 accessibility) */
      .eap-signal {{
        display: inline-flex;
        align-items: center;
        gap: 6px;
        padding: 4px 10px;
        border-radius: 6px;
        font-family: {FONTS["data"]};
        font-size: {TYPE_SCALE["label"]};
        font-weight: 600;
        min-height: 28px;
      }}
      .eap-signal-buy    {{ background: rgba(34,197,94,0.18); color: var(--badge-success); border: 1px solid var(--badge-success); }}
      .eap-signal-hold   {{ background: rgba(156,163,175,0.18); color: var(--muted); border: 1px solid var(--border); }}
      .eap-signal-sell   {{ background: rgba(239,68,68,0.18); color: var(--badge-danger); border: 1px solid var(--badge-danger); }}

      /* Tap targets ≥ 44px per CLAUDE.md §8 */
      button, [role="button"], .stButton > button {{
        min-height: 44px;
        font-family: {FONTS["ui"]};
        font-weight: 500;
      }}

      /* Disclosure pill for "Hypothetical results" etc. */
      .eap-disclosure {{
        background: rgba(245,158,11,0.10);
        border-left: 3px solid var(--warning);
        padding: 10px 14px;
        border-radius: 6px;
        font-size: {TYPE_SCALE["label"]};
        color: var(--text);
        margin: 8px 0;
      }}

      /* ── Data-source transparency badges (Day-3 first-class UX) ── */
      .eap-dss-badge {{
        display: inline-flex;
        align-items: center;
        gap: 6px;
        padding: 3px 10px;
        border-radius: 12px;
        font-family: {FONTS["data"]};
        font-size: {TYPE_SCALE["label"]};
        font-weight: 500;
        border: 1px solid transparent;
      }}
      .eap-dss-fallback {{
        background: rgba(245,158,11,0.18);
        color: var(--badge-warning);
        border-color: var(--badge-warning);
      }}
      .eap-dss-banner {{
        background: rgba(245,158,11,0.12);
        border-left: 3px solid var(--warning);
        color: var(--text);
        padding: 10px 14px;
        border-radius: 6px;
        font-size: {TYPE_SCALE["body"]};
        margin: 6px 0;
      }}
      .eap-dss-footnote {{
        display: inline-block;
        font-family: {FONTS["data"]};
        color: var(--muted);
        font-size: {TYPE_SCALE["label"]};
        padding: 2px 0;
      }}
    </style>
    """


def apply_theme() -> None:
    """Inject theme CSS. Safe to call on every page."""
    if "theme" not in st.session_state:
        st.session_state["theme"] = "dark"
    st.markdown(_css_for_theme(current_theme()), unsafe_allow_html=True)
