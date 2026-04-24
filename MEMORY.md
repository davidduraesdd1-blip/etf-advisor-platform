# Memory Index

Session continuity log. Newest entries on top. See master-template §16.

---

## 2026-04-23 — Baseline deployment verification (§25 Part A + B)

**Context:** First pass of §25 against live deploy at
https://etf-advisor.streamlit.app/.

### Part A — automated smoke test

`python tests/verify_deployment.py --env prod` → **9/9 checks passed**
- base URL reachable (1.57s, HTTP 200)
- no Python error signatures in landing
- expected shell markers present
- /Dashboard, /Portfolio, /ETF_Detail, /Methodology, /Settings all 200
- health endpoint /_stcore/health 200

### Part B — manual 20-point walkthrough (user-walked in browser)

- Universal 1-7, 9-20: all ✓
- **Item 8 — Level selector persistence: ⚠ BROKEN.** Selector resets on
  page navigation. Tracked as DV-1 in `pending_work.md`.
- **Item C1 — Performance display time horizons: ⚠ INCOMPLETE.** Not all
  of 1Y/3Y/5Y/since-inception shown on every performance view. Tracked
  as DV-2 in `pending_work.md`.
- Items C2-C8: all ✓ (including C7 drawdown calm messaging — earlier
  code-grep conclusion that C7 was missing was WRONG; user confirmed
  it's implemented, grep just missed the wording variant)
- X1 ✓ (`EXTENDED_MODULES_ENABLED=False` default)
- X2 ✓ (preview banner present — my earlier grep-based claim that it
  was missing was WRONG; user confirmed it renders correctly)
- Data feeds D1-D3: all ✓

### Summary

**Status: HEALTHY deploy, 2 feature bugs.** No deploy-level blockers.
Two real bugs found by user's browser walk:
- DV-1 (item 8): level selector doesn't persist across pages.
- DV-2 (item C1): performance displays missing required time horizons.

Both tracked at top of `pending_work.md`. Ship before Friday demo per §22.

### Lesson for future verifications

Code grep is a starting point, not an authority. User's browser
walkthrough is the ground truth for feature presence. When grep and
user-walk disagree, user-walk wins — the wording variant, the
conditional-load, or the runtime-computed string can hide what grep
can't see.

### Resume point

Next session: work DV-2 (time horizons — touches multiple views, needs
a shared helper). DV-1 resolved below.

---

## 2026-04-23 — DV-1 resolved (level selector persistence)

**Root cause:** `_render_sidebar()` was private to `app.py` and called
only from `app.py`. Streamlit's multipage convention runs the chosen
page script when the user navigates, NOT `app.py` — so the sidebar
(and its level selector) never rendered on pages other than the
landing view. `st.session_state["user_level"]` was fine; the value
was persisting correctly in state, but there was no widget rendered
on other pages to reflect it.

**Fix:**
1. Extracted sidebar into `ui/sidebar.py` as public `render_sidebar()`
   with an explicit `key="user_level_radio"` on the radio (guards
   against per-page widget-hash drift).
2. `app.py` imports `render_sidebar` and calls it from `main()`.
3. All 5 pages under `pages/` now `from ui.sidebar import render_sidebar`
   and call it immediately after `apply_theme()`.
4. Added `tests/test_sidebar_persistence.py` — uses Streamlit's
   `AppTest` runner to verify (a) `user_level` is initialized on first
   render, (b) a selection survives across re-renders, (c) a static
   check that every page under `pages/` imports and calls
   `render_sidebar()`. This static guard prevents the regression on
   any future page addition.

**Audit (§4 seven criteria):**
- Correctness: ✓ simulated + covered by new test
- Tests: ✓ 4 new assertions, static guard included
- Optimization: ✓ no new expensive calls; sidebar runs once per page
- Efficiency: ✓ no duplicate state writes
- Accuracy: ✓ session_state behavior per Streamlit docs
- Speed: ✓ sidebar render is ~milliseconds
- UI/UX: ✓ brand header, level selector, theme toggle, refresh button
  now visible on every page

**Files touched:**
- New: `ui/sidebar.py`, `tests/test_sidebar_persistence.py`
- Modified: `app.py`, `pages/01_Dashboard.py`, `pages/02_Portfolio.py`,
  `pages/03_ETF_Detail.py`, `pages/98_Methodology.py`,
  `pages/99_Settings.py`

**Next:** User tests locally, confirms selector persists across pages,
then this commits + pushes + redeploys + re-walks the 20-point check.
