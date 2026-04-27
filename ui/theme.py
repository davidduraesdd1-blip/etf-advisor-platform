"""
Theme + global CSS injection.

Call `apply_theme()` once at the top of every page. Reads the user's theme
choice from session_state ("dark" or "light"; "dark" is the default per
CLAUDE.md §8).

CLAUDE.md governance: Section 8 (design standards).

──────────────────────────────────────────────────────────────────────
2026-04-25 LEGACY-CSS AUDIT (advisor-2026-05 redesign Commit 1)

This file is the LEGACY compat layer. The redesign's primary stylesheet
is split between `ui/design_system.py` (tokens + base) and `ui/overrides.py`
(Streamlit widget overrides). The two run BEFORE this file inside
apply_theme(), so anything below paints on top.

Status of rules below:
  (a) load-bearing — keep:
      - :root legacy variables (--primary/--card/--text/--bg/--muted/--border/
        --badge-*) — referenced by every .eap-* class and dozens of inline
        styles in components.py + ds_components.py + page bodies. Cannot
        delete without sweeping inline-style usage.
      - html/body bg+color (lines 66-70) — sets text-rendering baseline
        for legacy widgets. DS sets the same for `[class*=css]` and `.stApp`
        but not the html/body element directly, so this still has a job.
      - Typography floors (lines 77-93) — CLAUDE.md §8 contract.
      - .eap-card / .eap-signal-* / .eap-disclosure / .eap-dss-* (lines
        95-176) — used pervasively by legacy components.py / page bodies.
      - button min-height 44px (lines 126-131) — CLAUDE.md §8 tap-target
        floor. DS doesn't replicate.

  (b) duplicate / conflicts with DS — DELETED in this commit:
      - [data-testid="stSidebar"] background-color: var(--card) !important
        on lines 72-75. ui/overrides.py L30-39 already styles the sidebar
        with var(--bg-1) (the DS token). The legacy rule was painting on
        top with the old --card token, defeating the DS sidebar palette.
        Removed; DS owns sidebar chrome.

  (c) flagged for follow-up post-demo (DO NOT delete now):
      - The dual variable system (--card/--bg/--text vs --bg-1/--bg-0/
        --text-primary). Eventually inline styles should migrate to DS
        tokens and this :root block can collapse to a thin alias layer.
        Out of scope for May 1 demo.
──────────────────────────────────────────────────────────────────────
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
    # 2026-04-26 audit-round-1 bonus 3: the legacy --primary/--card/--bg/
    # --text/--muted/--border tokens now ALIAS the design-system tokens
    # injected by ui/design_system.py::inject_theme. Existing `.eap-*`
    # rules + inline styles consuming these legacy names keep working;
    # they pick up the family-aligned advisor palette without duplicate
    # source. The light/dark badge variants stay since the DS doesn't
    # ship light-mode-aware badge text colors yet.
    if theme == "light":
        badge_success = COLORS["success_on_light"]
        badge_danger = COLORS["danger_on_light"]
        badge_warning = COLORS["warning_on_light"]
    else:
        badge_success = COLORS["success"]
        badge_danger = COLORS["danger"]
        badge_warning = COLORS["warning"]

    return f"""
    <style>
      @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap');

      /* Legacy alias layer — collapse 1:1 onto the design-system tokens
         from ui/design_system.py. Single source of truth for color.
         The --badge-* variants stay distinct because they're light-mode
         dimmed for WCAG AA on tinted backgrounds. */
      :root {{
        --primary: var(--accent);
        --bg:      var(--bg-0);
        --card:    var(--bg-1);
        --text:    var(--text-primary);
        --muted:   var(--text-muted);
        /* --border, --success, --danger, --warning, --info already shipped
           by design_system.py — no alias needed here. */
        --badge-success: {badge_success};
        --badge-danger:  {badge_danger};
        --badge-warning: {badge_warning};
      }}

      /* ── Mobile responsive (CLAUDE.md §8 — 768px breakpoint) ── */
      @media (max-width: 768px) {{
        /* Tighten card padding so KPI strips don't overflow horizontally. */
        .ds-card, .eap-card {{
          padding: 14px 16px;
        }}
        /* 4-up KPI strips collapse to 2-up on phones. */
        .ds-strip {{
          grid-template-columns: repeat(2, 1fr) !important;
        }}
        /* Page-level grid columns collapse to single column. */
        [data-testid="stHorizontalBlock"] {{
          flex-wrap: wrap !important;
        }}
        /* Ensure tap targets stay ≥ 44px even when buttons crowd. */
        .stButton > button {{
          min-height: 44px;
          font-size: 14px;
        }}
        /* Hero card text scales down on phone. */
        .ds-page-title, h1 {{
          font-size: clamp(20px, 5vw, 28px) !important;
        }}
      }}

      /* ── ARIA / accessibility helpers ── */
      [data-testid="stSidebarNav"] [aria-current="page"] a {{
        outline: 2px solid var(--accent);
        outline-offset: 1px;
      }}
      .stButton > button:focus-visible {{
        outline: 2px solid var(--accent);
        outline-offset: 2px;
      }}
      /* Screen-reader-only helper class consumed via st.markdown HTML when
         a visual cue lacks text (e.g., color-only signal stripes are
         already shape-coded but we add aria-label fallbacks below). */
      .sr-only {{
        position: absolute !important;
        width: 1px !important; height: 1px !important;
        padding: 0 !important; margin: -1px !important;
        overflow: hidden !important; clip: rect(0,0,0,0) !important;
        white-space: nowrap !important; border: 0 !important;
      }}

      html, body, [data-testid="stAppViewContainer"] {{
        background-color: var(--bg) !important;
        color: var(--text) !important;
        font-family: {FONTS["ui"]};
      }}

      /* AUDIT (b): [data-testid="stSidebar"] background rule removed — duplicates
         and conflicts with ui/overrides.py L30-39 which uses var(--bg-1). DS owns
         sidebar chrome now. The 1px border-right on the sidebar is also set in
         overrides.py so we don't need it here either. */

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
    # ── 2026-05 redesign: inject the advisor-family design-system theme
    #     FIRST so the new CSS variables are the base; the legacy
    #     _css_for_theme below then layers on top for widgets not yet
    #     ported. Guarded — any import failure falls back to pre-redesign
    #     behavior (legacy theme only).
    try:
        from ui.design_system import inject_theme as _ds_inject_theme
        from ui.overrides import inject_streamlit_overrides as _ds_inject_overrides
        _ds_inject_theme("etf-advisor-platform", theme=current_theme())
        _ds_inject_overrides()
        # 2026-04-26 hotfix: do NOT call render_sidebar_brand here.
        # ui/sidebar.py::render_sidebar() already renders the brand block
        # ("◐ ETF Advisor / Crypto ETF portfolio platform"); calling it
        # here too produced a duplicate "A · Advisor · family office"
        # block above the real brand. Single source of truth: sidebar.py.
    except Exception:
        pass
    st.markdown(_css_for_theme(current_theme()), unsafe_allow_html=True)
