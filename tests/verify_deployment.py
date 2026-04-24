"""
tests/verify_deployment.py — ETF Advisor Platform deployment smoke test

Customized from common/verify_deployment.py. Implements Part A of master
template Section 25.

Usage:
    python tests/verify_deployment.py --env prod
    python tests/verify_deployment.py --env local

Exits 0 on full pass, 1 on any failure. Run after every push to main.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urljoin

import requests

# ── PROJECT CUSTOMIZATION — edit these when you copy into tests/ ────────

DEFAULT_URLS = {
    "prod": "https://etf-advisor.streamlit.app/",
    "local": "http://localhost:8501/",
}

# Pages under pages/ — Streamlit Cloud strips the NN_ ordering prefix and
# serves the file stem as the path (underscores preserved). If a route
# 404s, try the alternate form (e.g. /01_Dashboard) in a follow-up run.
PAGES: list[str] = [
    "/Dashboard",
    "/Portfolio",
    "/ETF_Detail",
    "/Methodology",
    "/Settings",
]

# Strings that MUST appear in the landing-page response body.
#
# NOTE: Streamlit renders the UI client-side over WebSocket after the
# initial HTTP response, so sidebar labels and rendered content DON'T
# appear in a plain requests.get() response. These markers confirm the
# Streamlit shell is actually being served (not a 404 / outage page /
# Streamlit Cloud "sleeping" banner / broken deploy).
#
# For deeper checks (rendered DOM content, button clickability, theme
# toggle), use Part B — the 20-point browser checklist at
# shared-docs/deployment-checklists/etf-advisor-platform.md.
EXPECTED_STRINGS: list[str] = [
    "streamlit",   # Streamlit's HTML uses lowercase "streamlit" in multiple places
    "<script",     # confirms a real HTML shell, not a plain error page
    "root",        # Streamlit mounts on <div id="root">
]

# Signatures that indicate failure even when HTTP=200:
# Python errors surfaced to the page body, Streamlit exceptions, AND
# the Streamlit Cloud "app asleep" / unavailable banners.
ERROR_SIGNATURES: list[str] = [
    "Traceback (most recent call last)",
    "ModuleNotFoundError",
    "ImportError",
    "NameError:",
    "AttributeError:",
    "KeyError:",
    "TypeError:",
    "ValueError:",
    "IndexError:",
    "Streamlit exception",
    "<class 'Exception'>",
    # Streamlit Cloud asleep / parked / broken banners:
    "This app has gone to sleep",
    "Yes, get this app back up!",
    "App is currently unavailable",
]

# Response-time targets (seconds).
COLD_START_TIMEOUT_S = 60
WARM_PAGE_TIMEOUT_S = 5

# Optional: a Streamlit health endpoint (present in recent Streamlit
# versions). Set to None to skip.
HEALTH_ENDPOINT: Optional[str] = "/_stcore/health"

# ── IMPLEMENTATION — don't edit below unless changing the protocol ──────


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str = ""
    elapsed_s: float = 0.0


@dataclass
class VerifyReport:
    env: str
    base_url: str
    results: list[CheckResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(r.passed for r in self.results)

    def add(self, result: CheckResult) -> None:
        self.results.append(result)

    def print_summary(self) -> None:
        print(f"\n── deploy verify: {self.env} @ {self.base_url} ──")
        for r in self.results:
            mark = "✓" if r.passed else "✗"
            timing = f"{r.elapsed_s:5.2f}s"
            print(f"  {mark}  {timing}  {r.name}")
            if r.detail:
                for line in r.detail.splitlines():
                    print(f"          {line}")
        total = len(self.results)
        passed = sum(1 for r in self.results if r.passed)
        print(f"── {passed}/{total} checks passed ──")


def _get(url: str, timeout: float) -> tuple[Optional[requests.Response], float]:
    start = time.monotonic()
    try:
        resp = requests.get(url, timeout=timeout, allow_redirects=True)
        return resp, time.monotonic() - start
    except requests.RequestException as e:
        print(f"    request error: {e}", file=sys.stderr)
        return None, time.monotonic() - start


def check_reachable(report: VerifyReport, timeout_s: float) -> None:
    resp, elapsed = _get(report.base_url, timeout=timeout_s)
    ok = bool(resp and resp.status_code == 200)
    detail = f"HTTP {resp.status_code}" if resp else "no response"
    report.add(CheckResult(
        name=f"base URL reachable (<= {timeout_s:.0f}s)",
        passed=ok,
        detail=detail,
        elapsed_s=elapsed,
    ))


def check_no_errors_in_landing(report: VerifyReport) -> None:
    resp, elapsed = _get(report.base_url, timeout=WARM_PAGE_TIMEOUT_S)
    if resp is None:
        report.add(CheckResult(
            name="landing page — no Python error signatures",
            passed=False,
            detail="no response to re-check",
            elapsed_s=elapsed,
        ))
        return
    body = resp.text
    hits = [sig for sig in ERROR_SIGNATURES if sig in body]
    report.add(CheckResult(
        name="landing page — no Python error signatures",
        passed=not hits,
        detail=("clean" if not hits else f"found: {hits}"),
        elapsed_s=elapsed,
    ))


def check_expected_strings(report: VerifyReport) -> None:
    if not EXPECTED_STRINGS:
        report.add(CheckResult(
            name="landing page — expected strings",
            passed=True,
            detail="(EXPECTED_STRINGS empty — customize per project)",
        ))
        return
    resp, elapsed = _get(report.base_url, timeout=WARM_PAGE_TIMEOUT_S)
    if resp is None:
        report.add(CheckResult(
            name="landing page — expected strings",
            passed=False,
            detail="no response",
            elapsed_s=elapsed,
        ))
        return
    missing = [s for s in EXPECTED_STRINGS if s not in resp.text]
    report.add(CheckResult(
        name=f"landing page — expected strings ({len(EXPECTED_STRINGS)})",
        passed=not missing,
        detail=("all present" if not missing else f"missing: {missing}"),
        elapsed_s=elapsed,
    ))


def check_pages(report: VerifyReport) -> None:
    if not PAGES:
        report.add(CheckResult(
            name="all pages render (0 configured)",
            passed=True,
            detail="(PAGES empty — customize per project)",
        ))
        return
    for path in PAGES:
        url = urljoin(report.base_url, path.lstrip("/"))
        resp, elapsed = _get(url, timeout=WARM_PAGE_TIMEOUT_S)
        ok = bool(resp and resp.status_code == 200)
        body = resp.text if resp else ""
        errors = [sig for sig in ERROR_SIGNATURES if sig in body]
        if errors:
            ok = False
        detail_parts = []
        if resp:
            detail_parts.append(f"HTTP {resp.status_code}")
        if errors:
            detail_parts.append(f"errors: {errors}")
        report.add(CheckResult(
            name=f"page {path}",
            passed=ok,
            detail=" · ".join(detail_parts) or "ok",
            elapsed_s=elapsed,
        ))


def check_health_endpoint(report: VerifyReport) -> None:
    if not HEALTH_ENDPOINT:
        return
    url = urljoin(report.base_url, HEALTH_ENDPOINT.lstrip("/"))
    resp, elapsed = _get(url, timeout=WARM_PAGE_TIMEOUT_S)
    ok = bool(resp and resp.status_code == 200)
    detail = f"HTTP {resp.status_code}" if resp else "no response"
    report.add(CheckResult(
        name=f"health endpoint {HEALTH_ENDPOINT}",
        passed=ok,
        detail=detail,
        elapsed_s=elapsed,
    ))


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify a deployed app")
    parser.add_argument(
        "--env", choices=list(DEFAULT_URLS.keys()), default="prod",
        help="Which env to verify (prod / local / ...)",
    )
    parser.add_argument(
        "--url", default=None,
        help="Override base URL (wins over --env default)",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Emit JSON report instead of text",
    )
    args = parser.parse_args()

    base_url = args.url or DEFAULT_URLS[args.env]
    if not base_url.endswith("/"):
        base_url += "/"

    report = VerifyReport(env=args.env, base_url=base_url)

    # Cold-start reachability
    check_reachable(report, timeout_s=COLD_START_TIMEOUT_S)
    # Now the warm checks
    check_no_errors_in_landing(report)
    check_expected_strings(report)
    check_pages(report)
    check_health_endpoint(report)

    if args.json:
        print(json.dumps({
            "env": report.env,
            "base_url": report.base_url,
            "passed": report.passed,
            "checks": [
                {
                    "name": r.name,
                    "passed": r.passed,
                    "detail": r.detail,
                    "elapsed_s": round(r.elapsed_s, 3),
                }
                for r in report.results
            ],
        }, indent=2))
    else:
        report.print_summary()

    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())
    check_no_errors_in_landing(report)
    check_expected_strings(report)
    check_pages(report)
    check_health_endpoint(report)

    if args.json:
        print(json.dumps({
            "env": report.env,
            "base_url": report.base_url,
            "passed": report.passed,
            "checks": [
                {
                    "name": r.name,
                    "passed": r.passed,
                    "detail": r.detail,
                    "elapsed_s": round(r.elapsed_s, 3),
                }
                for r in report.results
            ],
        }, indent=2))
    else:
        report.print_summary()

    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())

