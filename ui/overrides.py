"""
ui/overrides.py — Streamlit widget CSS overrides for the advisor family.

Call inject_streamlit_overrides() once per page, after apply_theme() and
inject_theme() have run (both inject CSS variables; this file consumes them).

Advisor family distinctions from the sibling apps:
- Looser breathing room: rail 256px, gap 20px, card padding 24px.
- Serif display font for headings (Source Serif 4).
- Warmer charcoal dark mode, paper-white light mode.
"""
from __future__ import annotations


def inject_streamlit_overrides() -> None:
    try:
        import streamlit as st
    except ImportError:
        return

    css = """
    /* ─── Advisor-family design-system shell overrides ─── */

    section.main > div.block-container {
      padding-top: 20px;
      padding-bottom: 80px;
      max-width: none;
    }

    [data-testid="stSidebar"] {
      background: var(--bg-1) !important;
      border-right: 1px solid var(--border) !important;
      min-width: var(--rail-w) !important;
      max-width: calc(var(--rail-w) + 24px) !important;
    }
    [data-testid="stSidebar"] > div:first-child {
      padding: 18px 14px !important;
      background: var(--bg-1) !important;
    }

    /* Brand block */
    .ds-rail-brand {
      display: flex; align-items: center; gap: 12px;
      padding: 8px 10px 22px;
      font-family: var(--font-display);
      font-weight: 500; font-size: 17px; letter-spacing: -0.015em;
      color: var(--text-primary);
    }
    .ds-brand-dot {
      width: 26px; height: 26px; border-radius: 7px;
      display: grid; place-items: center;
      font-weight: 600; font-size: 12px;
    }

    /* Nav group header */
    .ds-nav-group {
      margin: 16px 0 6px; padding: 0 12px;
      color: var(--text-muted); font-size: 11px; font-weight: 500;
      letter-spacing: 0.08em; text-transform: uppercase;
    }

    /* Top bar */
    .ds-topbar {
      display: flex; align-items: center; gap: 12px;
      padding: 10px 4px 18px 4px;
      border-bottom: 1px solid var(--border);
      margin: -8px 0 20px 0;
    }
    .ds-crumbs { color: var(--text-muted); font-size: 13px; }
    .ds-crumbs b { color: var(--text-primary); font-weight: 500; }
    .ds-topbar-spacer { flex: 1; }

    .ds-level-group {
      display: inline-flex; align-items: center; gap: 0;
      background: var(--bg-1); border: 1px solid var(--border);
      border-radius: 8px; padding: 2px;
    }
    .ds-level-group button {
      all: unset; cursor: pointer;
      padding: 5px 12px; border-radius: 6px; font-size: 12.5px;
      color: var(--text-muted); font-weight: 500;
      font-family: var(--font-ui);
    }
    .ds-level-group button.on {
      background: var(--accent-soft); color: var(--text-primary);
    }
    .ds-chip-btn {
      all: unset; cursor: pointer;
      display: inline-flex; align-items: center; gap: 6px;
      background: var(--bg-1); border: 1px solid var(--border);
      border-radius: 8px; padding: 6px 12px; font-size: 13px;
      color: var(--text-secondary); font-family: var(--font-ui);
    }
    .ds-chip-btn:hover { border-color: var(--border-strong); color: var(--text-primary); }

    /* Page header — advisor variant uses serif display */
    .ds-page-hd {
      display: flex; justify-content: space-between; align-items: flex-end;
      gap: 18px; margin: 0 0 24px 0; flex-wrap: wrap;
    }
    .ds-page-title {
      margin: 0; font-size: 28px; font-weight: 500;
      font-family: var(--font-display); letter-spacing: -0.015em;
      color: var(--text-primary);
    }
    .ds-page-sub { color: var(--text-secondary); font-size: 14px; margin-top: 6px; }

    /* Data-source pills */
    .ds-row { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }
    .ds-pill {
      display: inline-flex; align-items: center; gap: 6px;
      font-size: 11.5px; padding: 3px 8px; border-radius: 999px;
      background: var(--bg-2); color: var(--text-secondary);
      border: 1px solid var(--border);
    }
    .ds-pill .tick { width: 6px; height: 6px; border-radius: 50%; background: var(--success); }
    .ds-pill.warn .tick { background: var(--warning); }
    .ds-pill.down .tick { background: var(--danger); }

    /* Card primitive + variants */
    .ds-card {
      background: var(--bg-1);
      border: 1px solid var(--border);
      border-radius: var(--card-radius);
      padding: var(--card-pad);
    }
    .ds-strip {
      display: grid; grid-template-columns: repeat(4, 1fr); gap: 0; padding: 0;
    }
    .ds-strip > div { padding: 14px 18px; border-right: 1px solid var(--border); }
    .ds-strip > div:last-child { border-right: none; }
    .ds-strip .lbl { font-size: 10.5px; color: var(--text-muted);
      text-transform: uppercase; letter-spacing: 0.05em; }
    .ds-strip .val { font-size: 18px; font-family: var(--font-mono);
      font-weight: 600; margin-top: 3px; color: var(--text-primary); }
    .ds-strip .sub { font-size: 11.5px; color: var(--text-muted);
      margin-top: 3px; font-family: var(--font-mono); }

    /* Streamlit native widgets inherit the advisor look */
    [data-testid="stMetric"] {
      background: var(--bg-1); border: 1px solid var(--border);
      border-radius: var(--card-radius); padding: 18px var(--card-pad);
    }
    [data-testid="stMetricLabel"] {
      color: var(--text-muted) !important;
      font-size: 11px !important; text-transform: uppercase;
      letter-spacing: 0.06em; font-weight: 500;
    }
    [data-testid="stMetricValue"] {
      font-family: var(--font-mono);
      font-size: 24px !important; font-weight: 600 !important;
      color: var(--text-primary) !important;
      line-height: 1.1;
    }
    [data-testid="stMetricDelta"] {
      font-family: var(--font-mono); font-size: 12.5px !important;
    }

    section.main [data-testid="stButton"] > button {
      background: var(--bg-1); color: var(--text-primary);
      border: 1px solid var(--border); border-radius: 8px;
      font-weight: 500; padding: 7px 16px;
    }
    section.main [data-testid="stButton"] > button:hover {
      border-color: var(--border-strong); background: var(--bg-2);
    }
    section.main [data-testid="stButton"] > button[kind="primary"] {
      background: var(--accent); color: var(--accent-ink);
      border-color: var(--accent);
    }

    [data-testid="stTextInput"] input,
    [data-testid="stNumberInput"] input,
    [data-testid="stSelectbox"] [data-baseweb="select"] > div {
      background: var(--bg-1) !important;
      color: var(--text-primary) !important;
      border-color: var(--border) !important;
    }

    [data-testid="stExpander"] {
      background: var(--bg-1); border: 1px solid var(--border);
      border-radius: var(--card-radius);
    }
    [data-testid="stExpander"] summary { color: var(--text-primary); }

    [data-testid="stTabs"] [data-baseweb="tab-list"] {
      gap: 6px; border-bottom: 1px solid var(--border);
    }
    [data-testid="stTabs"] button[role="tab"] {
      background: transparent; color: var(--text-muted);
      border-radius: 6px 6px 0 0; padding: 9px 16px;
      font-weight: 500; font-family: var(--font-ui);
    }
    [data-testid="stTabs"] button[role="tab"][aria-selected="true"] {
      color: var(--text-primary);
      border-bottom: 2px solid var(--accent);
    }

    [data-testid="stDataFrame"] {
      border: 1px solid var(--border); border-radius: var(--card-radius);
      overflow: hidden;
    }

    /* Advisor serif applies to native st.title / st.header too */
    section.main h1, section.main h2, section.main h3 {
      font-family: var(--font-display);
      letter-spacing: -0.015em;
      font-weight: 500;
    }

    @media (max-width: 768px) {
      [data-testid="stSidebar"] { min-width: 100% !important; max-width: 100% !important; }
      section.main > div.block-container { padding-top: 14px; padding-bottom: 48px; }
      .ds-strip { grid-template-columns: repeat(2, 1fr) !important; }
      .ds-strip > div { border-right: none; border-bottom: 1px solid var(--border); }
      .ds-strip > div:last-child { border-bottom: none; }
      .ds-page-hd { flex-direction: column; align-items: flex-start; }
      .ds-page-title { font-size: 24px; }
      .ds-level-group { display: none; }
    }
    """

    st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)
