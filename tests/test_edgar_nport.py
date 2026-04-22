"""
Day-4 tests for integrations.edgar_nport.

The XML parser is tested against a fixture that mirrors the shape of a
real SEC N-PORT filing. Live network paths are not exercised here —
they're covered indirectly by the scanner-health tests.
"""
from __future__ import annotations

from integrations.edgar_nport import (
    SUPPORTED_TICKERS,
    _extract_holding,
    get_etf_composition,
    parse_nport_xml,
)


# ═══════════════════════════════════════════════════════════════════════════
# Fixture — minimal N-PORT envelope with 2 investments
# ═══════════════════════════════════════════════════════════════════════════

_FIXTURE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<edgarSubmission xmlns="http://www.sec.gov/edgar/nport">
  <formData>
    <invstOrSecs>
      <invstOrSec>
        <name>BITCOIN</name>
        <title>Bitcoin (held in custody)</title>
        <balance>12345.6789</balance>
        <valUSD>500000000.00</valUSD>
        <pctVal>99.50</pctVal>
        <assetCat>DC</assetCat>
      </invstOrSec>
      <invstOrSec>
        <name>CASH-USD</name>
        <title>US Dollar operating cash</title>
        <balance>2500000.00</balance>
        <valUSD>2500000.00</valUSD>
        <pctVal>0.50</pctVal>
        <assetCat>FX</assetCat>
      </invstOrSec>
    </invstOrSecs>
  </formData>
</edgarSubmission>
"""


class TestParseNportXml:
    def test_returns_holdings_list(self):
        holdings = parse_nport_xml(_FIXTURE_XML)
        assert len(holdings) == 2

    def test_holdings_sorted_by_value_desc(self):
        holdings = parse_nport_xml(_FIXTURE_XML)
        assert holdings[0]["value_usd"] > holdings[1]["value_usd"]

    def test_field_extraction(self):
        holdings = parse_nport_xml(_FIXTURE_XML)
        btc = holdings[0]
        assert btc["name"] == "BITCOIN"
        assert btc["balance"] == 12345.6789
        assert btc["value_usd"] == 500_000_000.00
        assert btc["pct_value"] == 99.50
        assert btc["asset_cat"] == "DC"

    def test_malformed_xml_returns_empty(self):
        assert parse_nport_xml("<not-xml") == []

    def test_empty_filing_returns_empty_list(self):
        empty = """<?xml version="1.0"?>
        <edgarSubmission xmlns="http://www.sec.gov/edgar/nport">
            <formData><invstOrSecs></invstOrSecs></formData>
        </edgarSubmission>"""
        assert parse_nport_xml(empty) == []


class TestExtractHolding:
    def test_extracts_name_title_balance(self):
        import xml.etree.ElementTree as ET
        node = ET.fromstring(
            """<invstOrSec xmlns="http://www.sec.gov/edgar/nport">
                <name>ETHEREUM</name>
                <balance>100.5</balance>
                <valUSD>300000.0</valUSD>
            </invstOrSec>"""
        )
        h = _extract_holding(node)
        assert h is not None
        assert h["name"] == "ETHEREUM"
        assert h["balance"] == 100.5
        assert h["value_usd"] == 300000.0


class TestGetEtfCompositionUnsupportedTicker:
    """Unsupported tickers return an explicit placeholder — never fabricated data."""

    def test_unsupported_ticker_returns_empty_with_note(self):
        comp = get_etf_composition("XYZZY_NOT_A_TICKER")
        assert comp["supported"] is False
        assert comp["source"] == "unavailable"
        assert comp["holdings"] == []


class TestSupportedTickersSet:
    def test_supported_tickers_cover_day4_scope(self):
        # Planning-side directive: IBIT / ETHA / FBTC / FETH are in Day-4 scope
        for t in ["IBIT", "ETHA", "FBTC", "FETH"]:
            assert t in SUPPORTED_TICKERS


class TestSpotTrustCompositionPath:
    """
    Spot BTC / ETH ETFs are '33-Act grantor trusts — they do NOT file
    N-PORT. Composition for these comes from a curated registry that
    mirrors each issuer's prospectus data. These tests verify:
      - IBIT / FBTC / BITB / ETHA / FETH all route to the trust path
      - Returned shape has the underlying asset + cash buffer rows
      - Custodian and issuer holdings URL are populated
      - Source = "issuer_static" (not "edgar_live" / "cached" / etc.)
      - BYPASSES the conftest stub by reaching into the module directly.
    """

    def _call_real(self, ticker: str) -> dict:
        """
        Bypass the conftest stub by importing the module fresh and
        invoking the un-patched function. Relies on the fact that the
        TRUST_COMPOSITIONS path never hits the network.
        """
        import importlib
        import integrations.edgar_nport as real_mod
        importlib.reload(real_mod)
        return real_mod.get_etf_composition(ticker)

    def test_ibit_returns_bitcoin_trust_composition(self):
        comp = self._call_real("IBIT")
        assert comp["supported"] is True
        assert comp["source"] == "issuer_static"
        assert comp["holdings_count"] == 2
        assert comp["holdings"][0]["name"] == "Bitcoin"
        assert comp["holdings"][0]["pct_value"] == 99.5
        assert comp["holdings"][1]["name"] == "Cash and equivalents"
        assert "Coinbase" in comp["custodian"]
        assert comp["issuer_holdings_url"].startswith("https://")

    def test_feth_mentions_staking_enabled(self):
        comp = self._call_real("FETH")
        assert comp["supported"] is True
        assert comp["holdings"][0]["name"] == "Ethereum"
        assert "staking" in comp["note"].lower()

    def test_gbtc_high_fee_trust_still_in_registry(self):
        comp = self._call_real("GBTC")
        assert comp["supported"] is True
        assert comp["source"] == "issuer_static"

    def test_ethw_bitwise_trust_in_registry(self):
        comp = self._call_real("ETHW")
        assert comp["holdings"][0]["name"] == "Ethereum"

    def test_unsupported_after_new_routing(self):
        """Leveraged / income ETFs are NOT in SUPPORTED_TICKERS post-expansion."""
        comp = self._call_real("MSTY")
        assert comp["supported"] is False
        assert comp["holdings"] == []
