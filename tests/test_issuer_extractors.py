"""
test_issuer_extractors.py — Sprint 2.6 commits 1-3 coverage.

Verifies the per-issuer AUM extractors for BlackRock iShares /
Grayscale / ProShares. Mock the HTTP layer with monkeypatch so the
tests don't hit the live network — fixtures encode the actual HTML
patterns the smoke-test probe captured against the prod sites.

Also verifies the dispatcher routes correctly per issuer field, and
that the deferred issuers (Bitwise / Fidelity / Franklin Templeton)
return (None, None) gracefully — the chain falls through.

CLAUDE.md governance: §4 (test coverage), §22 (no-fallback honesty).
"""
from __future__ import annotations

import json
import pytest


# ─── Fixtures: realistic HTML/JSON snippets per issuer ───────────────────────

# BlackRock screener — minimal subset of the JSON shape with two of our
# tickers (IBIT, ETHA) populated, plus a non-our-universe entry to
# verify we don't false-match.
_BLACKROCK_SCREENER_JSON = {
    "333011": {
        "localExchangeTicker": "IBIT",
        "fundName": "iShares Bitcoin Trust ETF",
        "totalNetAssetsFund": {"d": "63,031,358,612", "r": 63031358612.12},
        "totalNetAssets":     {"d": "63,031,358,612", "r": 63031358612.12},
    },
    "338059": {
        "localExchangeTicker": "ETHA",
        "fundName": "iShares Ethereum Trust ETF",
        "totalNetAssetsFund": {"d": "7,375,302,028", "r": 7375302028.35},
    },
    "239619": {
        "localExchangeTicker": "AGG",
        "fundName": "iShares Core U.S. Aggregate Bond ETF",
        "totalNetAssetsFund": {"d": "120,000,000,000", "r": 120000000000.0},
    },
}

# Grayscale GBTC HTML — minimal snippet matching the regex pattern
# documented in the smoke-test probe.
_GRAYSCALE_GBTC_HTML = (
    "<html><body>"
    "<div class='split-table'>"
    "<span><p><strong>GAAP AUM </strong></p></span>"
    "<span class='split-table__cell'>"
    "<p class='MuiTypography-root'>$11,787,285,457</p></span>"
    "</div></body></html>"
)
# Newer Grayscale ETF (no GAAP qualifier).
_GRAYSCALE_GDLC_HTML = (
    "<html><body>"
    "<div class='split-table'>"
    "<span><p><strong>AUM </strong></p></span>"
    "<span class='split-table__cell'>"
    "<p class='MuiTypography-root'>$431,000,000</p></span>"
    "</div></body></html>"
)

# ProShares BITO HTML — snippet matching the snapshot-netAssets regex.
_PROSHARES_BITO_HTML = (
    "<html><body>"
    '<li class="about-fund__list-item mb-3">'
    '<span class="about-fund__list-label d-inline-block">Net Assets</span>'
    '<div><span id="snapshot-netAssets" class="about-fund__list-value '
    'd-inline-block">$1,932,237,020</span></div></li>'
    "</body></html>"
)
# ProShares BITI lives under leveraged-and-inverse path; same HTML shape.
_PROSHARES_BITI_HTML = _PROSHARES_BITO_HTML.replace("$1,932,237,020", "$179,500,000")


# ─── Test mock helpers ───────────────────────────────────────────────────────

class _MockResp:
    """Minimal Response stand-in for monkeypatched requests.get.
    When `json_obj` is provided, `text` is auto-populated with the
    JSON-serialized form so callers that use json.loads(resp.text)
    AND callers that use resp.json() both get the same payload."""
    def __init__(self, status: int, text: str = "", json_obj=None):
        self.status_code = status
        if json_obj is not None and not text:
            text = json.dumps(json_obj)
        self.text = text
        self._json = json_obj
    def json(self):
        if self._json is not None:
            return self._json
        return json.loads(self.text)


# ═══════════════════════════════════════════════════════════════════════════
# BlackRock iShares
# ═══════════════════════════════════════════════════════════════════════════

class TestBlackRockExtractor:
    def setup_method(self):
        # Clear the module cache between tests so each run starts fresh.
        from integrations import issuer_extractors as ie
        ie._BLACKROCK_SCREENER_CACHE.clear()

    def test_ibit_returns_total_net_assets_fund(self, monkeypatch):
        from integrations import issuer_extractors as ie
        # Monkeypatch the screener fetcher to return our fixture JSON.
        def _fake_get(url, headers=None, timeout=None):
            return _MockResp(200, json_obj=_BLACKROCK_SCREENER_JSON)
        import requests
        monkeypatch.setattr(requests, "get", _fake_get)
        v = ie.extract_blackrock_aum("IBIT")
        assert v == pytest.approx(63031358612.12, rel=1e-6)

    def test_etha_returns_total_net_assets_fund(self, monkeypatch):
        from integrations import issuer_extractors as ie
        import requests
        monkeypatch.setattr(
            requests, "get",
            lambda url, headers=None, timeout=None: _MockResp(200, json_obj=_BLACKROCK_SCREENER_JSON),
        )
        v = ie.extract_blackrock_aum("ETHA")
        assert v == pytest.approx(7375302028.35, rel=1e-6)

    def test_unknown_ticker_returns_none(self, monkeypatch):
        from integrations import issuer_extractors as ie
        import requests
        monkeypatch.setattr(
            requests, "get",
            lambda url, headers=None, timeout=None: _MockResp(200, json_obj=_BLACKROCK_SCREENER_JSON),
        )
        # IDOG is in our universe but not in the BlackRock screener fixture.
        assert ie.extract_blackrock_aum("IDOG") is None

    def test_screener_503_returns_none_gracefully(self, monkeypatch):
        from integrations import issuer_extractors as ie
        import requests
        monkeypatch.setattr(
            requests, "get",
            lambda url, headers=None, timeout=None: _MockResp(503, text="<html>maintenance</html>"),
        )
        assert ie.extract_blackrock_aum("IBIT") is None

    def test_screener_cached_for_process_lifetime(self, monkeypatch):
        from integrations import issuer_extractors as ie
        import requests
        call_count = [0]
        def _counting_get(url, headers=None, timeout=None):
            call_count[0] += 1
            return _MockResp(200, json_obj=_BLACKROCK_SCREENER_JSON)
        monkeypatch.setattr(requests, "get", _counting_get)
        ie.extract_blackrock_aum("IBIT")
        ie.extract_blackrock_aum("ETHA")
        ie.extract_blackrock_aum("IBIT")
        # Only ONE network fetch despite three lookups.
        assert call_count[0] == 1


# ═══════════════════════════════════════════════════════════════════════════
# Grayscale
# ═══════════════════════════════════════════════════════════════════════════

class TestGrayscaleExtractor:
    def test_gbtc_extracts_via_gaap_aum_pattern(self, monkeypatch):
        from integrations import issuer_extractors as ie
        import requests
        monkeypatch.setattr(
            requests, "get",
            lambda url, headers=None, timeout=None: _MockResp(200, text=_GRAYSCALE_GBTC_HTML),
        )
        v = ie.extract_grayscale_aum("GBTC")
        assert v == pytest.approx(11787285457.0, rel=1e-6)

    def test_gdlc_extracts_via_plain_aum_pattern(self, monkeypatch):
        from integrations import issuer_extractors as ie
        import requests
        monkeypatch.setattr(
            requests, "get",
            lambda url, headers=None, timeout=None: _MockResp(200, text=_GRAYSCALE_GDLC_HTML),
        )
        v = ie.extract_grayscale_aum("GDLC")
        assert v == pytest.approx(431_000_000.0, rel=1e-6)

    def test_404_status_returns_none(self, monkeypatch):
        from integrations import issuer_extractors as ie
        import requests
        monkeypatch.setattr(
            requests, "get",
            lambda url, headers=None, timeout=None: _MockResp(404, text="<html>404</html>"),
        )
        assert ie.extract_grayscale_aum("ADAX") is None

    def test_html_with_no_aum_pattern_returns_none(self, monkeypatch):
        from integrations import issuer_extractors as ie
        import requests
        monkeypatch.setattr(
            requests, "get",
            lambda url, headers=None, timeout=None: _MockResp(200, text="<html><p>nothing</p></html>"),
        )
        assert ie.extract_grayscale_aum("UNKNOWN") is None

    def test_dollar_amount_below_sanity_bound_rejected(self, monkeypatch):
        """A regex match for "$5" in marketing copy must not become a $5 AUM."""
        from integrations import issuer_extractors as ie
        import requests
        monkeypatch.setattr(
            requests, "get",
            lambda url, headers=None, timeout=None: _MockResp(200, text="<p>AUM minimum $5 trade</p>"),
        )
        assert ie.extract_grayscale_aum("ANY") is None


# ═══════════════════════════════════════════════════════════════════════════
# ProShares (dual-path fall-through)
# ═══════════════════════════════════════════════════════════════════════════

class TestProSharesExtractor:
    def test_strategic_path_wins_for_bito(self, monkeypatch):
        """First URL (strategic) returns 200 + valid pattern → no second fetch."""
        from integrations import issuer_extractors as ie
        import requests
        urls_called = []
        def _fake_get(url, headers=None, timeout=None):
            urls_called.append(url)
            return _MockResp(200, text=_PROSHARES_BITO_HTML)
        monkeypatch.setattr(requests, "get", _fake_get)
        v = ie.extract_proshares_aum("BITO")
        assert v == pytest.approx(1_932_237_020.0)
        assert len(urls_called) == 1
        assert "/strategic/bito" in urls_called[0]

    def test_falls_through_to_leveraged_for_biti(self, monkeypatch):
        """First URL (strategic) 404s → second URL (leveraged-and-inverse) used."""
        from integrations import issuer_extractors as ie
        import requests
        urls_called = []
        def _fake_get(url, headers=None, timeout=None):
            urls_called.append(url)
            if "strategic" in url:
                return _MockResp(404, text="<html>404</html>")
            return _MockResp(200, text=_PROSHARES_BITI_HTML)
        monkeypatch.setattr(requests, "get", _fake_get)
        v = ie.extract_proshares_aum("BITI")
        assert v == pytest.approx(179_500_000.0)
        assert len(urls_called) == 2
        assert "/strategic/biti" in urls_called[0]
        assert "/leveraged-and-inverse/biti" in urls_called[1]

    def test_both_paths_fail_returns_none(self, monkeypatch):
        from integrations import issuer_extractors as ie
        import requests
        monkeypatch.setattr(
            requests, "get",
            lambda url, headers=None, timeout=None: _MockResp(404, text="<html>404</html>"),
        )
        assert ie.extract_proshares_aum("EETU") is None


# ═══════════════════════════════════════════════════════════════════════════
# Dispatcher (extract_issuer_aum)
# ═══════════════════════════════════════════════════════════════════════════

class TestDispatcher:
    def test_blackrock_dispatch_returns_correct_source_label(self, monkeypatch):
        from integrations import issuer_extractors as ie
        ie._BLACKROCK_SCREENER_CACHE.clear()
        import requests
        monkeypatch.setattr(
            requests, "get",
            lambda url, headers=None, timeout=None: _MockResp(200, json_obj=_BLACKROCK_SCREENER_JSON),
        )
        v, src = ie.extract_issuer_aum("IBIT", "BlackRock iShares")
        assert v == pytest.approx(63031358612.12)
        assert src == "issuer-site:blackrock_ishares"

    def test_blackrock_alias_dispatches_same(self, monkeypatch):
        from integrations import issuer_extractors as ie
        ie._BLACKROCK_SCREENER_CACHE.clear()
        import requests
        monkeypatch.setattr(
            requests, "get",
            lambda url, headers=None, timeout=None: _MockResp(200, json_obj=_BLACKROCK_SCREENER_JSON),
        )
        # Universe sometimes uses bare "BlackRock" instead of "BlackRock iShares".
        v, src = ie.extract_issuer_aum("IBIT", "BlackRock")
        assert v == pytest.approx(63031358612.12)
        assert src == "issuer-site:blackrock_ishares"

    def test_grayscale_dispatch_returns_correct_source_label(self, monkeypatch):
        from integrations import issuer_extractors as ie
        import requests
        monkeypatch.setattr(
            requests, "get",
            lambda url, headers=None, timeout=None: _MockResp(200, text=_GRAYSCALE_GBTC_HTML),
        )
        v, src = ie.extract_issuer_aum("GBTC", "Grayscale")
        assert v == pytest.approx(11787285457.0)
        assert src == "issuer-site:grayscale"

    def test_proshares_dispatch_returns_correct_source_label(self, monkeypatch):
        from integrations import issuer_extractors as ie
        import requests
        monkeypatch.setattr(
            requests, "get",
            lambda url, headers=None, timeout=None: _MockResp(200, text=_PROSHARES_BITO_HTML),
        )
        v, src = ie.extract_issuer_aum("BITO", "ProShares")
        assert v == pytest.approx(1_932_237_020.0)
        assert src == "issuer-site:proshares"

    def test_bitwise_dispatch_returns_none_when_fetch_fails(self, monkeypatch):
        """Sprint 2.7 wired Bitwise via the per-fund-domain pattern
        (e.g. https://bitbetf.com/). When the fetch fails (DNS error,
        404, no JSON in body), the dispatcher returns (None, None) so
        the chain falls through cleanly. We monkeypatch requests.get
        to simulate a 404 and verify the graceful-None pair."""
        from integrations import issuer_extractors as ie
        import requests
        monkeypatch.setattr(
            requests, "get",
            lambda url, headers=None, timeout=None: _MockResp(404, text="Not Found"),
        )
        v, src = ie.extract_issuer_aum("BITB", "Bitwise")
        assert v is None
        assert src is None

    def test_fidelity_dispatch_returns_none_pair_gracefully(self):
        from integrations.issuer_extractors import extract_issuer_aum
        v, src = extract_issuer_aum("FBTC", "Fidelity")
        assert v is None
        assert src is None

    def test_franklin_dispatch_returns_none_pair_gracefully(self):
        from integrations.issuer_extractors import extract_issuer_aum
        v, src = extract_issuer_aum("EZBC", "Franklin Templeton")
        assert v is None
        assert src is None

    def test_unknown_issuer_returns_none_pair(self):
        from integrations.issuer_extractors import extract_issuer_aum
        v, src = extract_issuer_aum("XYZ", "SomeRandomIssuer LLC")
        assert v is None
        assert src is None
