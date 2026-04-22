# Deep audit — 2026-04-22 evening

Per CLAUDE.md §4 full-scope protocol. Second audit pass today; the
morning pass (`audit_2026_04_22.md`, 13 issues fixed) closed all
findings then. Since that pass we added:

- Batched yfinance fetch (`get_etf_prices_batch`)
- Precomputed analytics snapshot + nightly GH Actions cron
- Option 3 transparency layer (registry + panel + labeled badges)
- Allocation-table row-click navigation
- SuperGrok deep-links on Composition coin names
- Category filter on ETF Detail + ticker-count label
- MC chart readability pass + median-path overlay
- Streamlit `use_container_width=` → `width=` API migration
- Circuit-breaker snapshot-driven exemption

This audit looks for issues introduced by those changes plus anything
the morning pass missed.

## Scope executed

- Re-read every file touched today (8 source files + 1 new script + 1 workflow)
- Static bug-pattern scans: mutable defaults, bare `except`, `eval`/`exec`/`pickle`/`subprocess`, unsafe HTML without escape, missing request timeouts
- Dynamic verification:
  - Snapshot path vs live path math agreement (IBIT delta 0.04% return)
  - Live-path timing with snapshot hidden (batch warmup added)
  - Precompute regenerated end-to-end successfully, 73 / 73 forward + 61 / 73 live
- Test suite at start: 197 passing
- Test suite at end: 197 passing (no regressions)

## Findings

Severity legend: **C**ritical / **H**igh / **M**edium / **L**ow

| # | Sev | File | Issue | Fix |
|---|-----|------|-------|-----|
| 1 | H | core/etf_universe.py | Live-path fallback (snapshot missing/stale) did 200+ sequential yfinance calls without batching — ~5 min on a cold Streamlit Cloud boot if the snapshot is gone | Added batch pre-warm (5y + 144d all-ticker batches + BTC-USD/ETH-USD 10y) before the per-ticker enrichment loop |
| 2 | L | scripts/precompute_analytics.py | Dead import of `_CATEGORY_DEFAULTS` — never referenced | Removed |
| 3 | L | scripts/precompute_analytics.py | Snapshot metadata `tickers_with_data` conflated "fully live" (ret + vol + corr) with "forward-only" (only model estimate, yfinance unindexed) | Metadata now exposes both `tickers_fully_live` and `tickers_forward_only` counts so consumers can distinguish |

## Non-issues verified

The pattern scans came back clean on every prior concern:

- ✅ No `eval` / `exec` / `pickle.load` / `subprocess` usage anywhere
- ✅ No mutable default arguments (no `def f(x=[])` or `def f(x={})`)
- ✅ No bare `except:` or `except Exception: pass` silent swallowers (every handler either re-raises, returns a sentinel, or calls `logger.warning/error`)
- ✅ Every `requests.get` call has an explicit timeout
- ✅ All `unsafe_allow_html=True` sites either use static strings or run `html.escape()` on variable content
- ✅ All pyarrow column-dtype coercions use nullable `Int64` or uniform-type values
- ✅ The allocation-row-click → ETF Detail session-state hand-off uses `.pop()` so no stale selection sticks across navigation
- ✅ Data-sources panel `key=` is distinct per page so Streamlit doesn't collide

## Math re-verification

Re-ran the core math sanity checks:

- Snapshot path for IBIT: return 25.56%, vol 52.9%, forward 66.88%
- Live path for IBIT (snapshot hidden): return 25.60%, vol 52.92%, forward 66.9%
- Delta: 0.04% — within expected quantization noise
- Universe size 73, tickers with full live data 61, forward-only 12

Known residuals (flagged, not fixed):

- When yfinance is actively throttling the test environment, even the
  batch-warmup path takes minutes because the batch itself fails. This
  is a yfinance/network issue, not a code issue — the snapshot path is
  the real solution, running nightly via GH Actions.
- Row-click on ETF Detail: clicking the SAME ticker twice in a row on
  the Portfolio allocation table won't re-navigate (session-state
  guard prevents an infinite redirect loop after browser back). User
  can work around by clicking a different ticker first, or using the
  dropdown directly. Narrow edge case, not demo-blocking.

## Commit

- `05deceb` (earlier today) — log-quality fixes (deprecation noise, breaker
  false-trips, silent order skips)
- `this commit` — audit batch: live-path batch warmup + precompute metadata
  cleanup + dead-import removal

## Final status

- 197 / 197 tests passing
- Working tree clean after this commit
- All three audit findings resolved
- Morning pass + evening pass combined fixed 16 issues across the codebase today
