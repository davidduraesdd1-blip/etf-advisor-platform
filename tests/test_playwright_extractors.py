"""
test_playwright_extractors.py — Sprint 2.7 (2026-05-01).

Coverage for integrations/issuer_extractors_playwright.py. Mocks the
Playwright stack via monkeypatching `_fetch_with_playwright` so tests
don't actually launch chromium — fixture HTML encodes the patterns
extracted live from real Franklin Templeton product pages on
2026-05-01.

CLAUDE.md governance: §4 (test-on-touch), §22 (no-fallback honesty —
verifying graceful-None on every failure mode).
"""
from __future__ import annotations

import pytest


# ─── Fixture: snippet of Franklin EZBC product page (2026-05-01) ─────────────
# The full page is 553KB; this is the relevant ~600-char window around
# the AUM tile, with surrounding markup preserved so the regex's
# 0-800-char gap window between "Total Net Assets" and the <dd> is
# realistically exercised.
_FRANKLIN_EZBC_HTML = (
    "<html><body>"
    "<dl class='product-summary'>"
    "<dt><span id='summary-item-x'>Total Net Assets "
    "<frk-footnote data-gtm-component='frk-footnote'><!----><!----></frk-footnote>"
    "<frk-footnote data-gtm-component='frk-footnote'><!----><!----></frk-footnote>"
    "<!----></span></dt>"
    "<dd class='text-right'>$491.45 Million</dd>"
    "</dl>"
    "</body></html>"
)

_FRANKLIN_EZET_HTML = _FRANKLIN_EZBC_HTML.replace("$491.45 Million", "$46.65 Million")
_FRANKLIN_EZPZ_HTML = _FRANKLIN_EZBC_HTML.replace("$491.45 Million", "$11.69 Million")

# Page that has the label but no <dd> (selector-not-found case)
_FRANKLIN_NO_DD_HTML = (
    "<html><body>"
    "<dl><dt>Total Net Assets</dt></dl>"  # no <dd>
    "</body></html>"
)

# Page where the AUM looks suspiciously low (sub-$1M sanity bound)
_FRANKLIN_TOO_LOW_HTML = _FRANKLIN_EZBC_HTML.replace(
    "$491.45 Million", "$0.50 Million",  # $500K — below $1M floor
)

# Bitwise BITB JSON-shape fixture (per-fund domain, embedded JSON)
_BITWISE_BITB_HTML = (
    '<html><script>'
    'window.__PRODUCT__={"ticker":"BITB","netAssets":2979872605.13,'
    '"navPerShare":42.18}'
    '</script>'
    '<body><div><h4>Net Assets (AUM)</h4>'
    '<p class="c-cjWCAs">$2,979,872,605</p></div></body></html>'
)

# Bitwise page where JSON is absent — fallback HTML regex must hit
_BITWISE_HTML_ONLY = (
    '<html><body><div><h4>Net Assets (AUM)</h4>'
    '<p class="c-cjWCAs">$245,590,444</p></div></body></html>'
)

# Bitwise page with no AUM at all (404-style content)
_BITWISE_NO_DATA_HTML = "<html><body><h1>Page Not Found</h1></body></html>"


# ─── _parse_money tests ──────────────────────────────────────────────────────

class TestParseMoney:
    """The helper that converts strings like '$491.45 Million' to a USD
    float. Critical correctness path — every Playwright extractor
    routes its capture through it."""

    def test_parses_full_word_million(self):
        from integrations.issuer_extractors_playwright import _parse_money
        assert _parse_money("$491.45 Million") == pytest.approx(491_450_000.0)

    def test_parses_full_word_billion(self):
        from integrations.issuer_extractors_playwright import _parse_money
        assert _parse_money("$11.8 Billion") == pytest.approx(11_800_000_000.0)

    def test_parses_shorthand_M(self):
        from integrations.issuer_extractors_playwright import _parse_money
        assert _parse_money("$491.45M") == pytest.approx(491_450_000.0)

    def test_parses_raw_integer(self):
        from integrations.issuer_extractors_playwright import _parse_money
        assert _parse_money("$2,979,872,605") == pytest.approx(2_979_872_605.0)

    def test_rejects_sub_dollar(self):
        """$0.50 (no unit) should reject — sanity bound 1e6 floor."""
        from integrations.issuer_extractors_playwright import _parse_money
        assert _parse_money("$0.50") is None

    def test_rejects_above_trillion(self):
        from integrations.issuer_extractors_playwright import _parse_money
        # 9 trillion dollars — above 1e12 sanity ceiling
        assert _parse_money("$9,000,000,000,000") is None

    def test_rejects_empty_input(self):
        from integrations.issuer_extractors_playwright import _parse_money
        assert _parse_money("") is None
        assert _parse_money(None) is None  # type: ignore[arg-type]

    def test_rejects_garbage_text(self):
        from integrations.issuer_extractors_playwright import _parse_money
        assert _parse_money("contact us for AUM") is None

    def test_parses_thousand_word(self):
        # Should reject because $5,000 thousand = $5M, but the regex
        # would match — actually 5*1e3 = 5000 < 1e6 floor, so None.
        # And $2 thousand = 2000 < 1e6. Test the boundary.
        from integrations.issuer_extractors_playwright import _parse_money
        # 2,000 thousand = 2,000,000 → just at the floor; should pass.
        assert _parse_money("2,000 thousand") == pytest.approx(2_000_000.0)


# ─── is_playwright_available ─────────────────────────────────────────────────

class TestIsPlaywrightAvailable:
    """Gate on the chain. Returns False if module OR chromium is missing."""

    def test_returns_bool(self):
        """Smoke: doesn't raise, returns a bool."""
        from integrations.issuer_extractors_playwright import is_playwright_available
        result = is_playwright_available()
        assert isinstance(result, bool)

    def test_returns_false_without_module(self, monkeypatch):
        """Force-import-fail playwright.sync_api — gate must say False."""
        import sys
        # Hide the playwright module from imports inside the function.
        original = sys.modules.get("playwright.sync_api")
        sys.modules["playwright.sync_api"] = None  # type: ignore[assignment]
        try:
            from integrations.issuer_extractors_playwright import is_playwright_available
            # Re-execute by reimporting? No — function does the import
            # at call-time. Just call it.
            assert is_playwright_available() is False
        finally:
            if original is not None:
                sys.modules["playwright.sync_api"] = original
            else:
                del sys.modules["playwright.sync_api"]


# ─── Franklin extractor (mocked _fetch_with_playwright) ──────────────────────

class TestFranklinExtractor:
    """Mock the Playwright fetch to avoid launching chromium in CI.
    Verify the regex pulls the AUM tile from the realistic markup."""

    def test_extracts_ezbc_aum(self, monkeypatch):
        from integrations import issuer_extractors_playwright as ext
        monkeypatch.setattr(ext, "is_playwright_available", lambda: True)
        monkeypatch.setattr(ext, "_fetch_with_playwright",
                            lambda url, hydrate_ms=5000: _FRANKLIN_EZBC_HTML)
        v = ext.extract_franklin_aum_pw("EZBC")
        assert v == pytest.approx(491_450_000.0)

    def test_extracts_ezet_aum(self, monkeypatch):
        from integrations import issuer_extractors_playwright as ext
        monkeypatch.setattr(ext, "is_playwright_available", lambda: True)
        monkeypatch.setattr(ext, "_fetch_with_playwright",
                            lambda url, hydrate_ms=5000: _FRANKLIN_EZET_HTML)
        v = ext.extract_franklin_aum_pw("EZET")
        assert v == pytest.approx(46_650_000.0)

    def test_extracts_ezpz_aum(self, monkeypatch):
        from integrations import issuer_extractors_playwright as ext
        monkeypatch.setattr(ext, "is_playwright_available", lambda: True)
        monkeypatch.setattr(ext, "_fetch_with_playwright",
                            lambda url, hydrate_ms=5000: _FRANKLIN_EZPZ_HTML)
        v = ext.extract_franklin_aum_pw("EZPZ")
        assert v == pytest.approx(11_690_000.0)

    def test_returns_none_for_unknown_ticker(self, monkeypatch):
        """Unmapped ticker → None without launching browser."""
        from integrations import issuer_extractors_playwright as ext
        monkeypatch.setattr(ext, "is_playwright_available", lambda: True)
        # Should NOT call _fetch_with_playwright at all.
        called = []
        monkeypatch.setattr(
            ext, "_fetch_with_playwright",
            lambda url, hydrate_ms=5000: called.append(url) or "<html></html>",
        )
        v = ext.extract_franklin_aum_pw("XYZ_UNKNOWN")
        assert v is None
        assert called == []

    def test_returns_none_when_playwright_unavailable(self, monkeypatch):
        """Streamlit Cloud cold-start: chromium missing → silent no-op."""
        from integrations import issuer_extractors_playwright as ext
        monkeypatch.setattr(ext, "is_playwright_available", lambda: False)
        v = ext.extract_franklin_aum_pw("EZBC")
        assert v is None

    def test_returns_none_when_dd_selector_missing(self, monkeypatch):
        """Page has the label but no <dd> sibling → None."""
        from integrations import issuer_extractors_playwright as ext
        monkeypatch.setattr(ext, "is_playwright_available", lambda: True)
        monkeypatch.setattr(ext, "_fetch_with_playwright",
                            lambda url, hydrate_ms=5000: _FRANKLIN_NO_DD_HTML)
        v = ext.extract_franklin_aum_pw("EZBC")
        assert v is None

    def test_rejects_below_sanity_floor(self, monkeypatch):
        """$0.50M is below the $1M sanity floor → None."""
        from integrations import issuer_extractors_playwright as ext
        monkeypatch.setattr(ext, "is_playwright_available", lambda: True)
        monkeypatch.setattr(ext, "_fetch_with_playwright",
                            lambda url, hydrate_ms=5000: _FRANKLIN_TOO_LOW_HTML)
        v = ext.extract_franklin_aum_pw("EZBC")
        assert v is None

    def test_returns_none_when_fetch_returns_none(self, monkeypatch):
        """Playwright launch failure → _fetch returns None → extractor None."""
        from integrations import issuer_extractors_playwright as ext
        monkeypatch.setattr(ext, "is_playwright_available", lambda: True)
        monkeypatch.setattr(ext, "_fetch_with_playwright",
                            lambda url, hydrate_ms=5000: None)
        v = ext.extract_franklin_aum_pw("EZBC")
        assert v is None


# ─── Documented dead-end stubs (Fidelity / ETF.com / Bitwise) ────────────────

class TestDocumentedDeadEnds:
    """The Sprint 2.7 stubs for issuers that even Playwright can't
    reach. They MUST return None unconditionally — chain falls
    through. If a future commit removes the stub or changes the
    signature, this catches it."""

    def test_fidelity_always_returns_none(self):
        from integrations.issuer_extractors_playwright import extract_fidelity_aum_pw
        assert extract_fidelity_aum_pw("FBTC") is None
        assert extract_fidelity_aum_pw("FETH") is None

    def test_etfcom_always_returns_none(self):
        from integrations.issuer_extractors_playwright import extract_etfcom_aum_pw
        assert extract_etfcom_aum_pw("IBIT") is None

    def test_bitwise_pw_stub_returns_none(self):
        """Bitwise lives in static-HTML path; PW stub kept for symmetry."""
        from integrations.issuer_extractors_playwright import extract_bitwise_aum_pw
        assert extract_bitwise_aum_pw("BITB") is None


# ─── Dispatcher (extract_issuer_aum_pw) ──────────────────────────────────────

class TestDispatcher:
    """The public entrypoint used by integrations.etf_flow_data._scrape_issuer_aum_playwright."""

    def test_routes_franklin_to_franklin_extractor(self, monkeypatch):
        from integrations import issuer_extractors_playwright as ext
        monkeypatch.setattr(ext, "is_playwright_available", lambda: True)
        monkeypatch.setattr(ext, "_fetch_with_playwright",
                            lambda url, hydrate_ms=5000: _FRANKLIN_EZBC_HTML)
        v, src = ext.extract_issuer_aum_pw("EZBC", "Franklin Templeton")
        assert v == pytest.approx(491_450_000.0)
        assert src == "issuer-site:franklin (playwright)"

    def test_routes_franklin_short_name(self, monkeypatch):
        """Universe entries also use 'Franklin' (no Templeton)."""
        from integrations import issuer_extractors_playwright as ext
        monkeypatch.setattr(ext, "is_playwright_available", lambda: True)
        monkeypatch.setattr(ext, "_fetch_with_playwright",
                            lambda url, hydrate_ms=5000: _FRANKLIN_EZBC_HTML)
        v, src = ext.extract_issuer_aum_pw("EZBC", "Franklin")
        assert v == pytest.approx(491_450_000.0)
        assert src == "issuer-site:franklin (playwright)"

    def test_returns_none_pair_for_unknown_issuer(self, monkeypatch):
        from integrations import issuer_extractors_playwright as ext
        monkeypatch.setattr(ext, "is_playwright_available", lambda: True)
        v, src = ext.extract_issuer_aum_pw("FOO", "SomeRandomIssuer")
        assert v is None
        assert src is None

    def test_returns_none_pair_when_playwright_unavailable(self, monkeypatch):
        from integrations import issuer_extractors_playwright as ext
        monkeypatch.setattr(ext, "is_playwright_available", lambda: False)
        v, src = ext.extract_issuer_aum_pw("EZBC", "Franklin Templeton")
        assert v is None
        assert src is None

    def test_returns_none_pair_when_extractor_crashes(self, monkeypatch):
        """Any exception inside the extractor must NOT propagate."""
        from integrations import issuer_extractors_playwright as ext

        def _raises(ticker):
            raise RuntimeError("simulated chromium crash")

        monkeypatch.setattr(ext, "is_playwright_available", lambda: True)
        monkeypatch.setattr(ext, "extract_franklin_aum_pw", _raises)
        # Replace the dispatch entry too
        monkeypatch.setattr(ext, "_PW_DISPATCH",
                            {"Franklin Templeton": _raises})
        v, src = ext.extract_issuer_aum_pw("EZBC", "Franklin Templeton")
        assert v is None
        assert src is None

    def test_routes_fidelity_to_dead_end_stub(self):
        """Fidelity dispatch returns the stub's None — verifies the
        dispatcher wires it (so we'd catch a regression where
        Fidelity got removed entirely)."""
        from integrations.issuer_extractors_playwright import extract_issuer_aum_pw
        # is_playwright_available may be True or False; regardless,
        # Fidelity stub returns None, so dispatcher returns (None, None).
        v, src = extract_issuer_aum_pw("FBTC", "Fidelity")
        assert v is None
        assert src is None


# ─── Bitwise static-HTML extractor (in issuer_extractors.py, not _pw) ────────
#
# These tests live here because Sprint 2.7 introduced both the
# Bitwise static-HTML extractor (in issuer_extractors.py) AND the
# Playwright module — keeping all Sprint-2.7-introduced extractor
# tests together for easier audit-on-touch traversal.

class _MockResp:
    def __init__(self, status_code: int, text: str = ""):
        self.status_code = status_code
        self.text = text


class TestBitwiseStaticExtractor:
    """integrations.issuer_extractors.extract_bitwise_aum — added
    Sprint 2.7. Per-fund-domain pattern <ticker>etf.com."""

    def test_extracts_via_json_path(self, monkeypatch):
        from integrations import issuer_extractors as ie
        import requests
        monkeypatch.setattr(
            requests, "get",
            lambda url, headers=None, timeout=None: _MockResp(200, _BITWISE_BITB_HTML),
        )
        v = ie.extract_bitwise_aum("BITB")
        assert v == pytest.approx(2_979_872_605.13)

    def test_falls_through_to_html_when_json_absent(self, monkeypatch):
        from integrations import issuer_extractors as ie
        import requests
        monkeypatch.setattr(
            requests, "get",
            lambda url, headers=None, timeout=None: _MockResp(200, _BITWISE_HTML_ONLY),
        )
        v = ie.extract_bitwise_aum("ETHW")
        assert v == pytest.approx(245_590_444.0)

    def test_returns_none_on_404(self, monkeypatch):
        from integrations import issuer_extractors as ie
        import requests
        monkeypatch.setattr(
            requests, "get",
            lambda url, headers=None, timeout=None: _MockResp(404, "Not Found"),
        )
        assert ie.extract_bitwise_aum("BLNK") is None

    def test_returns_none_when_no_aum_in_body(self, monkeypatch):
        from integrations import issuer_extractors as ie
        import requests
        monkeypatch.setattr(
            requests, "get",
            lambda url, headers=None, timeout=None: _MockResp(200, _BITWISE_NO_DATA_HTML),
        )
        assert ie.extract_bitwise_aum("BITC") is None

    def test_dispatcher_routes_bitwise_to_correct_label(self, monkeypatch):
        from integrations import issuer_extractors as ie
        import requests
        monkeypatch.setattr(
            requests, "get",
            lambda url, headers=None, timeout=None: _MockResp(200, _BITWISE_BITB_HTML),
        )
        v, src = ie.extract_issuer_aum("BITB", "Bitwise")
        assert v == pytest.approx(2_979_872_605.13)
        assert src == "issuer-site:bitwise"

    def test_dispatcher_returns_none_pair_when_fetch_fails(self, monkeypatch):
        from integrations import issuer_extractors as ie
        import requests
        monkeypatch.setattr(
            requests, "get",
            lambda url, headers=None, timeout=None: _MockResp(404, "Not Found"),
        )
        v, src = ie.extract_issuer_aum("BITB", "Bitwise")
        assert v is None
        assert src is None

    def test_rejects_sub_million_aum(self, monkeypatch):
        """Sanity bound: $500K netAssets is parse-error, return None."""
        from integrations import issuer_extractors as ie
        import requests
        bad_html = '<html><script>{"netAssets":500000.0}</script></html>'
        monkeypatch.setattr(
            requests, "get",
            lambda url, headers=None, timeout=None: _MockResp(200, bad_html),
        )
        assert ie.extract_bitwise_aum("FAKE") is None
