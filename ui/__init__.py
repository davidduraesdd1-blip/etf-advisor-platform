"""ui/ — UI package for etf-advisor-platform.

Pre-redesign modules: components, level_helpers, sidebar, theme.
2026-05 redesign modules: design_system, overrides, ds_components.
"""
from .design_system import (
    inject_theme,
    tokens,
    ACCENTS,
    kpi_tile,
    signal_badge,
    data_source_badge,
    compliance_callout,
)
from .overrides import inject_streamlit_overrides
from .ds_components import (
    render_sidebar_brand,
    render_top_bar,
    page_header,
    kpi_strip,
)

__all__ = [
    "inject_theme",
    "tokens",
    "ACCENTS",
    "kpi_tile",
    "signal_badge",
    "data_source_badge",
    "compliance_callout",
    "inject_streamlit_overrides",
    "render_sidebar_brand",
    "render_top_bar",
    "page_header",
    "kpi_strip",
]
