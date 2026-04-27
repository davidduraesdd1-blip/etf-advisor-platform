"""
common/ui_design_system.py — Shared design-system tokens for the 4-app redesign.

This module is the single source of truth for the new visual design.
Claude Code in each project should COPY this file to
<project>/ui/design_system.py and leave the common/ copy as the
canonical reference per master-template §19.

Usage inside a Streamlit app:

    from ui.design_system import inject_theme, tokens

    inject_theme("crypto-signal-app")         # first call on every page
    st.markdown(f"## {tokens.BRAND_NAME}")    # etc.

Reference mockups:
    shared-docs/design-mockups/sibling-family-<app>-*.html
    shared-docs/design-mockups/advisor-etf-*.html

──────────────────────────────────────────────────────────────────────
2026-04-25 LEGACY-CSS AUDIT (advisor-2026-05 redesign Commit 1)

ZERO `!important` rules in this file. This is the BASE layer:
  * :root variable definitions (--accent, --bg-0…3, --text-primary,
    --border, --font-ui/mono/display, --rail-w, --card-radius, --gap)
  * thin set of element-level base rules (html/body/*.stApp font + bg,
    .num/[data-testid="stMetricValue"] tabular nums, h1/h2/h3 serif
    for advisor family, .ds-card primitive)
  * 4 component helpers returning HTML strings (kpi_tile, signal_badge,
    data_source_badge, compliance_callout) — no CSS in those, just
    inline-style HTML using DS tokens.

Verdict: clean. KEEP as-is.
──────────────────────────────────────────────────────────────────────
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal


# ── Per-app accent tokens ────────────────────────────────────────────

AppId = Literal[
    "crypto-signal-app",
    "flare-defi-model",
    "rwa-infinity-model",
    "etf-advisor-platform",
]

ACCENTS: dict[AppId, dict[str, str]] = {
    "crypto-signal-app": {
        "accent":      "#22d36f",   # signal-green
        "accent_soft": "rgba(34, 211, 111, 0.12)",
        "accent_ink":  "#0a0a0f",
    },
    "flare-defi-model": {
        "accent":      "#1d4ed8",   # flare-blue
        "accent_soft": "rgba(29, 78, 216, 0.14)",
        "accent_ink":  "#f8f8fc",
    },
    "rwa-infinity-model": {
        "accent":      "#d4a54c",   # rwa-amber
        "accent_soft": "rgba(212, 165, 76, 0.14)",
        "accent_ink":  "#0a0a0f",
    },
    "etf-advisor-platform": {
        # Advisor family — separate palette + serif display
        "accent":      "#0fa68a",   # muted advisor-teal
        "accent_soft": "rgba(15, 166, 138, 0.12)",
        "accent_ink":  "#0c0d12",
    },
}


# ── Shared grayscale ladders ─────────────────────────────────────────
# Sibling family — cooler, pure black base
SIBLING_DARK = {
    "bg_0": "#0a0a0f", "bg_1": "#121218", "bg_2": "#1a1a22", "bg_3": "#2a2a34",
    "text_primary": "#e8e8f0", "text_secondary": "#8a8a9d", "text_muted": "#5d5d6e",
    "border": "#2a2a34", "border_strong": "#3d3d4a",
}
SIBLING_LIGHT = {
    "bg_0": "#fafafb", "bg_1": "#ffffff", "bg_2": "#f5f5f7", "bg_3": "#e8e9ed",
    "text_primary": "#0f1014", "text_secondary": "#545660", "text_muted": "#8b8d96",
    "border": "#e8e9ed", "border_strong": "#d1d3d9",
}
# Advisor family — warmer charcoal base + paper-white light mode
ADVISOR_DARK = {
    "bg_0": "#0c0d12", "bg_1": "#14161d", "bg_2": "#1d1f28", "bg_3": "#2e303c",
    "text_primary": "#e8e9ee", "text_secondary": "#9aa0ab", "text_muted": "#666875",
    "border": "#2e303c", "border_strong": "#454854",
}
ADVISOR_LIGHT = {
    "bg_0": "#f6f5f2", "bg_1": "#ffffff", "bg_2": "#faf9f6", "bg_3": "#ebeae6",
    "text_primary": "#13151b", "text_secondary": "#4a4c56", "text_muted": "#7e808a",
    "border": "#e4e2dd", "border_strong": "#d1cec7",
}

# Semantic (shared)
SEMANTIC = {
    "success": "#22c55e", "danger": "#ef4444",
    "warning": "#f59e0b", "info": "#3b82f6",
}


# ── Typography & layout tokens ───────────────────────────────────────

@dataclass(frozen=True)
class Tokens:
    # Shared across all apps
    font_ui: str = "'Inter', system-ui, -apple-system, sans-serif"
    font_mono: str = "'JetBrains Mono', ui-monospace, monospace"
    # Advisor-only — serif display
    font_display: str = "'Source Serif 4', Georgia, serif"

    # Sibling defaults
    rail_w: str = "240px"
    topbar_h: str = "56px"
    card_radius: str = "12px"
    card_pad: str = "16px"
    gap: str = "16px"

    # Advisor overrides (looser breathing room)
    advisor_rail_w: str = "256px"
    advisor_topbar_h: str = "60px"
    advisor_card_radius: str = "10px"
    advisor_card_pad: str = "24px"
    advisor_gap: str = "20px"


tokens = Tokens()


# ── Family classifier ────────────────────────────────────────────────

def family_of(app: AppId) -> Literal["sibling", "advisor"]:
    return "advisor" if app == "etf-advisor-platform" else "sibling"


# ── Streamlit theme injector ─────────────────────────────────────────

def inject_theme(app: AppId, theme: Literal["dark", "light"] = "dark") -> None:
    """
    Inject the full design-system CSS into a Streamlit page. Call once
    at the top of every page (after st.set_page_config), before any
    other st.* calls that need the theme applied.

    Streamlit's built-in theming is limited; we shadow it with our own
    CSS tokens for consistency with the mockups.
    """
    try:
        import streamlit as st
    except ImportError:
        raise RuntimeError("inject_theme() requires streamlit")

    accent = ACCENTS[app]
    fam = family_of(app)
    dark = SIBLING_DARK if fam == "sibling" else ADVISOR_DARK
    light = SIBLING_LIGHT if fam == "sibling" else ADVISOR_LIGHT
    scale = dark if theme == "dark" else light

    css = _build_css(app, fam, accent, scale, theme)
    st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)


def _build_css(app: AppId, fam: str, accent: dict, scale: dict, theme: str) -> str:
    """Assemble the CSS variable block + a compact set of base rules."""
    is_advisor = fam == "advisor"
    rail_w = tokens.advisor_rail_w if is_advisor else tokens.rail_w
    topbar_h = tokens.advisor_topbar_h if is_advisor else tokens.topbar_h
    card_radius = tokens.advisor_card_radius if is_advisor else tokens.card_radius
    card_pad = tokens.advisor_card_pad if is_advisor else tokens.card_pad
    gap = tokens.advisor_gap if is_advisor else tokens.gap

    return f"""
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600{'&family=Source+Serif+4:opsz,wght@8..60,400;8..60,500;8..60,600' if is_advisor else ''}&display=swap');

    :root {{
      --accent: {accent["accent"]};
      --accent-soft: {accent["accent_soft"]};
      --accent-ink: {accent["accent_ink"]};
      --bg-0: {scale["bg_0"]};
      --bg-1: {scale["bg_1"]};
      --bg-2: {scale["bg_2"]};
      --bg-3: {scale["bg_3"]};
      --text-primary: {scale["text_primary"]};
      --text-secondary: {scale["text_secondary"]};
      --text-muted: {scale["text_muted"]};
      --border: {scale["border"]};
      --border-strong: {scale["border_strong"]};
      --success: {SEMANTIC["success"]};
      --danger: {SEMANTIC["danger"]};
      --warning: {SEMANTIC["warning"]};
      --info: {SEMANTIC["info"]};
      --font-ui: {tokens.font_ui};
      --font-mono: {tokens.font_mono};
      --font-display: {tokens.font_display};
      --rail-w: {rail_w};
      --topbar-h: {topbar_h};
      --card-radius: {card_radius};
      --card-pad: {card_pad};
      --gap: {gap};
    }}

    /* Override Streamlit defaults */
    html, body, [class*="css"] {{
      font-family: var(--font-ui);
      color: var(--text-primary);
      background: var(--bg-0);
    }}
    .stApp {{ background: var(--bg-0); }}

    /* Tabular nums for everything that looks like a number */
    .num, [data-testid="stMetricValue"] {{
      font-family: var(--font-mono);
      font-variant-numeric: tabular-nums;
    }}

    /* Serif headings for advisor family */
    {'h1, h2, h3 { font-family: var(--font-display); font-weight: 500; letter-spacing: -0.015em; }' if is_advisor else ''}

    /* Card primitive */
    .ds-card {{
      background: var(--bg-1);
      border: 1px solid var(--border);
      border-radius: var(--card-radius);
      padding: var(--card-pad);
    }}
    """


# ── Component helpers (HTML-string returning) ────────────────────────
#
# 2026-04-26 audit-round-1 bonus 7: these helpers RETURN HTML strings.
# The Streamlit-direct equivalents live in ui/components.py and CALL
# st.markdown / st.metric. Naming kept here for backward-compat with
# the common/ template; aliases below export the *_html variants for
# callers who want the distinction explicit.

def kpi_tile(label: str, value: str, delta: str | None = None,
             delta_direction: Literal["up", "down", "neutral"] = "neutral") -> str:
    """Return HTML for a KPI tile. Render via st.markdown(html, unsafe_allow_html=True)."""
    delta_class = {"up": "color: var(--success);",
                   "down": "color: var(--danger);",
                   "neutral": "color: var(--text-muted);"}[delta_direction]
    delta_html = f'<div style="font-size:12px;font-family:var(--font-mono);margin-top:4px;{delta_class}">{delta}</div>' if delta else ""
    return f"""
    <div class="ds-card" style="display:flex;flex-direction:column;gap:4px;">
      <div style="font-size:11px;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.06em;">{label}</div>
      <div style="font-size:22px;font-weight:600;font-family:var(--font-mono);line-height:1.1;">{value}</div>
      {delta_html}
    </div>
    """


def signal_badge(signal: Literal["BUY", "HOLD", "SELL"]) -> str:
    """BUY/HOLD/SELL badge with shape + color per CLAUDE.md §8 (never color-only)."""
    cfg = {
        "BUY":  ("▲", "success"),
        "HOLD": ("■", "warning"),
        "SELL": ("▼", "danger"),
    }[signal]
    shape, sem = cfg
    return f"""
    <span style="
      display:inline-flex;align-items:center;gap:6px;
      padding:6px 12px;border-radius:999px;
      font-weight:600;font-size:13px;letter-spacing:0.05em;
      background:color-mix(in srgb, var(--{sem}) 16%, transparent);
      color:var(--{sem});
    ">{shape} {signal}</span>
    """


def data_source_badge(label: str, status: Literal["live", "cached", "down"] = "live") -> str:
    """Inline data-source transparency pill."""
    tick_color = {"live": "var(--success)", "cached": "var(--warning)", "down": "var(--danger)"}[status]
    return f"""
    <span style="
      display:inline-flex;align-items:center;gap:6px;
      font-size:11.5px;padding:3px 8px;border-radius:999px;
      background:var(--bg-2);color:var(--text-secondary);border:1px solid var(--border);
    ">
      <span style="width:6px;height:6px;border-radius:50%;background:{tick_color};"></span>
      {label} · {status}
    </span>
    """


def compliance_callout(title: str, body: str, link_label: str | None = None, link_url: str = "#") -> str:
    """Left-stripe compliance callout used on ETF advisor performance displays."""
    link_html = f' <a href="{link_url}" style="color:var(--accent);text-decoration:none;font-weight:500;">{link_label}</a>' if link_label else ""
    return f"""
    <div style="
      display:flex;gap:14px;align-items:flex-start;
      padding:16px 20px;
      background:color-mix(in srgb, var(--accent) 5%, var(--bg-1));
      border:1px solid color-mix(in srgb, var(--accent) 20%, var(--border));
      border-left:3px solid var(--accent);
      border-radius:8px;
      font-size:13px;
    ">
      <div style="
        width:22px;height:22px;border-radius:50%;
        background:var(--accent-soft);color:var(--accent);
        display:grid;place-items:center;font-weight:600;font-size:13px;flex-shrink:0;
      ">i</div>
      <div>
        <div style="font-weight:500;color:var(--text-primary);margin-bottom:2px;">{title}</div>
        {body}{link_html}
      </div>
    </div>
    """


# ── Disambiguating aliases (audit-round-1 bonus 7) ───────────────────
# Encourage callers to use the *_html names so it's obvious they're
# getting an HTML-string back, not a Streamlit-direct render. The
# Streamlit-direct ``signal_badge`` and ``kpi_tile`` continue to live
# in ui/components.py.
kpi_tile_html = kpi_tile
signal_badge_html = signal_badge
data_source_badge_html = data_source_badge
compliance_callout_html = compliance_callout


# ── Test helper ──────────────────────────────────────────────────────

if __name__ == "__main__":
    # Quick smoke — just dump the CSS for each app so we can eyeball it
    for app in ACCENTS.keys():
        print(f"─── {app} ───")
        print(_build_css(
            app,
            family_of(app),
            ACCENTS[app],
            SIBLING_DARK if family_of(app) == "sibling" else ADVISOR_DARK,
            "dark",
        )[:500])
        print()
