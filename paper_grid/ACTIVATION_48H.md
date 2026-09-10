# Activation evidence — 11 September 2026

- Start: **2026-09-11 02:11:55 Europe/Bucharest**.
- Deadline: **2026-09-13 02:11:55 Europe/Bucharest** (exactly 48 hours).
- Dashboard: <http://127.0.0.1:8873/> — loopback only, available on this Mac.
- launchd service `com.danslab.zmarty-paper48` is loaded in user session 501.
- First shared public-data cycle completed at 02:12:02 local time.
- Both independent virtual accounts started at 1,000 USDT, with zero positions,
  fees or completed trades. The original ledger was backed up and paused.
- Initial CoinGlass history was usable for BTR, NES and SAGA. NIULAI and 2U2 had
  fewer than 25 completed hourly observations and were blocked in the filtered
  account. This data limitation is visible in the report; it is not a hidden fill.
- Service restart test passed: start/end times and both complete account states
  remained unchanged, and the worker resumed successfully.
- Native Grok half-hour review is active; its manual run completed with
  `Succeeded` in the routine history. The superseded daily writer is disabled.
- The committed-secret gate passed on the implementation revision: 2,234 tracked
  files scanned. No source or runtime credentials were published.
- Offline gate: **92 tests passed**, including deadline checks around collection
  and settlement, paired rollback, locks, duplicate runs, state/code integrity,
  new-symbol feature coverage, modeled accounting and read-only HTTP routes.
- Python compilation and whitespace checks passed. Desktop Chrome and the narrow
  in-app browser rendered actual values and timestamps. The refresh control
  retrieved the new snapshot; a visible warning identified limited CoinGlass data.
- Browser verification covered the actual desktop and narrow panel dimensions;
  an exhaustive device/browser matrix was not run. Live exchange execution and
  profitability were not tested or enabled.

The engine is already running; future performance must be read from the report,
not inferred from this initial activation record. No historic intervals will be
replayed as fictitious forward trades after interruptions.
