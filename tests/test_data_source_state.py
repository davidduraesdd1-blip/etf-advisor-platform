"""
Day-3 tests for core.data_source_state.

Critical: simulate a full fallback cascade, verify get_state() transitions
correctly through all four states (LIVE → FALLBACK_LIVE → CACHED → STATIC).
"""
from __future__ import annotations

import pytest

from core.data_source_state import (
    DataSourceState,
    get_age_minutes,
    get_source,
    get_state,
    mark_cache_hit,
    mark_static_fallback,
    register_fetch_attempt,
    reset_all,
    snapshot,
)


@pytest.fixture(autouse=True)
def _clean_state():
    reset_all()
    yield
    reset_all()


class TestInitialStateIsUnknown:
    def test_untouched_category_is_unknown(self):
        assert get_state("etf_price") == DataSourceState.UNKNOWN
        assert get_source("etf_price") == ""
        assert get_age_minutes("etf_price") is None


class TestLiveState:
    def test_primary_source_success_sets_live(self):
        register_fetch_attempt("etf_price", "yfinance", success=True)
        assert get_state("etf_price") == DataSourceState.LIVE
        assert get_source("etf_price") == "yfinance"

    def test_fred_primary_for_risk_free_rate(self):
        register_fetch_attempt("risk_free_rate", "fred", success=True)
        assert get_state("risk_free_rate") == DataSourceState.LIVE


class TestFallbackLiveState:
    def test_secondary_source_success_sets_fallback_live(self):
        register_fetch_attempt("etf_price", "yfinance", success=False)
        register_fetch_attempt("etf_price", "stooq", success=True)
        assert get_state("etf_price") == DataSourceState.FALLBACK_LIVE
        assert get_source("etf_price") == "stooq"

    def test_state_machine_accepts_arbitrary_source_names(self):
        # The DSS state machine is source-agnostic — it accepts any
        # string as a source label. Runtime only currently calls it
        # with {yfinance, stooq, edgar, fred, cache, static}, but this
        # keeps the door open for paid-tier sources (Alpha Vantage,
        # Polygon, Kaiko) reactivating without DSS changes.
        register_fetch_attempt("etf_price", "yfinance", success=False)
        register_fetch_attempt("etf_price", "polygon", success=True)
        assert get_state("etf_price") == DataSourceState.FALLBACK_LIVE
        assert get_source("etf_price") == "polygon"


class TestCachedState:
    def test_mark_cache_hit_sets_cached(self):
        mark_cache_hit("etf_price", age_seconds=420)
        assert get_state("etf_price") == DataSourceState.CACHED
        # age_minutes read from cache_age_seconds_at_mark
        age = get_age_minutes("etf_price")
        assert age is not None and age >= 7


class TestStaticState:
    def test_mark_static_fallback_sets_static(self):
        mark_static_fallback("risk_free_rate", note="FRED unavailable")
        assert get_state("risk_free_rate") == DataSourceState.STATIC
        assert get_source("risk_free_rate") == "static"


class TestFullCascade:
    """Simulate the full fallback chain end-to-end."""

    def test_cascade_live_to_fallback_to_cached_to_static(self):
        # Step 1: primary succeeds → LIVE
        register_fetch_attempt("etf_price", "yfinance", success=True)
        assert get_state("etf_price") == DataSourceState.LIVE

        # Step 2: primary fails, secondary succeeds → FALLBACK_LIVE
        register_fetch_attempt("etf_price", "yfinance", success=False)
        register_fetch_attempt("etf_price", "stooq", success=True)
        assert get_state("etf_price") == DataSourceState.FALLBACK_LIVE

        # Step 3: all live fail, serve cache → CACHED
        register_fetch_attempt("etf_price", "yfinance", success=False)
        register_fetch_attempt("etf_price", "stooq", success=False)
        mark_cache_hit("etf_price", age_seconds=180)
        assert get_state("etf_price") == DataSourceState.CACHED

        # Step 4: no cache either → STATIC
        mark_static_fallback("etf_price", note="no cache, no live")
        assert get_state("etf_price") == DataSourceState.STATIC

    def test_recovery_back_to_live(self):
        """After a cascade, a successful primary fetch should restore LIVE."""
        mark_static_fallback("etf_price", note="fallback")
        assert get_state("etf_price") == DataSourceState.STATIC
        register_fetch_attempt("etf_price", "yfinance", success=True)
        assert get_state("etf_price") == DataSourceState.LIVE


class TestSnapshotIntegration:
    def test_snapshot_includes_all_touched_categories(self):
        register_fetch_attempt("etf_price", "yfinance", success=True)
        mark_static_fallback("risk_free_rate", note="x")
        snap = snapshot()
        assert "etf_price" in snap
        assert "risk_free_rate" in snap
        assert snap["etf_price"]["state"] == "LIVE"
        assert snap["risk_free_rate"]["state"] == "STATIC"

    def test_reset_all_clears_snapshot(self):
        register_fetch_attempt("etf_price", "yfinance", success=True)
        assert snapshot()
        reset_all()
        assert snapshot() == {}


class TestMetricDependenciesRegistry:
    """
    Option 3 transparency layer — every data-source category feeds
    one or more user-facing metrics. The registry names them so the
    badge + panel can tell the FA exactly which numbers are affected
    when a category enters fallback.
    """

    def test_every_primary_category_has_dependencies(self):
        """
        Every key in _PRIMARY_SOURCE_BY_CATEGORY must have a
        corresponding entry in METRIC_DEPENDENCIES — otherwise a
        category that enters fallback would render a generic
        "this panel" message instead of naming the affected metrics.
        """
        from core.data_source_state import (
            METRIC_DEPENDENCIES,
            _PRIMARY_SOURCE_BY_CATEGORY,
        )
        missing = set(_PRIMARY_SOURCE_BY_CATEGORY) - set(METRIC_DEPENDENCIES)
        assert not missing, (
            f"Categories with no METRIC_DEPENDENCIES entry: {missing}. "
            f"Every category that reports state must name at least one "
            f"consumer metric."
        )

    def test_dependencies_are_non_empty_lists(self):
        from core.data_source_state import METRIC_DEPENDENCIES
        for cat, metrics in METRIC_DEPENDENCIES.items():
            assert isinstance(metrics, list), f"{cat} must map to a list"
            assert len(metrics) >= 1, f"{cat} must have ≥1 consumer metric"
            for m in metrics:
                assert isinstance(m, str) and m, f"{cat} has non-string metric"

    def test_affected_metrics_returns_copy(self):
        """Mutating the returned list must not corrupt the registry."""
        from core.data_source_state import METRIC_DEPENDENCIES, affected_metrics
        original_len = len(METRIC_DEPENDENCIES.get("etf_price", []))
        returned = affected_metrics("etf_price")
        returned.append("SHOULD_NOT_APPEAR")
        assert len(METRIC_DEPENDENCIES["etf_price"]) == original_len

    def test_affected_metrics_unknown_returns_empty(self):
        from core.data_source_state import affected_metrics
        assert affected_metrics("made_up_category") == []

    def test_human_category_label_has_label_for_every_category(self):
        from core.data_source_state import (
            METRIC_DEPENDENCIES,
            human_category_label,
        )
        for cat in METRIC_DEPENDENCIES:
            label = human_category_label(cat)
            assert isinstance(label, str) and len(label) > len(cat), (
                f"human_category_label({cat!r}) should return a longer "
                f"human-friendly string, got {label!r}"
            )

    def test_human_category_label_fallback_is_raw_string(self):
        """Unknown category returns its own name rather than raising."""
        from core.data_source_state import human_category_label
        assert human_category_label("made_up_category") == "made_up_category"
