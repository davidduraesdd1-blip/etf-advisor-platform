#!/usr/bin/env bash
# 2026-05-01 Sprint 2.7 — Streamlit Cloud post-install hook.
#
# Streamlit Cloud auto-runs setup.sh AFTER `pip install -r
# requirements.txt` and BEFORE the app boots. We use it to install
# chromium for Playwright-based AUM extractors (Franklin Templeton +
# any future JS-rendered issuer pages).
#
# Failure here is non-fatal — the chain in
# integrations/issuer_extractors_playwright.py runtime-probes
# `is_playwright_available()` and silently falls through if chromium
# is missing. CLAUDE.md §10 (no-fallback), §22 (graceful degradation).
set -e
python -m playwright install chromium || {
    echo "[setup.sh] chromium install failed — Playwright extractors will silently no-op"
    exit 0
}
echo "[setup.sh] chromium installed for Playwright"
