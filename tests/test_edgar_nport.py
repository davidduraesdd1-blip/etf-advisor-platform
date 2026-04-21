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
        assert "IBIT" in comp["note"]    # mentions supported tickers


class TestSupportedTickersSet:
    def test_supported_tickers_cover_day4_scope(self):
        # Planning-side directive: IBIT / ETHA / FBTC / FETH are in Day-4 scope
        for t in ["IBIT", "ETHA", "FBTC", "FETH"]:
            assert t in SUPPORTED_TICKERS
