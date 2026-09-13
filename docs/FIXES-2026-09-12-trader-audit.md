# Trader Dashboard Fix Pack — 2026-09-12 (Codex handoff)

> **Status:** FINAL — all fixes implemented, verified in staging and live.
> Branch: `fix/2026-09-12-trader-audit`, commit `d9d2f33` (Kimi lane,
> user-authorized ZCode boundary override). Codex: review, merge per repo flow.

## 1. Context

User-reported audit of https://danslabtrader.vercel.app/#positions ("is it
real? gaps and bugs? design/cards/charts not professional"). Audit verdict:
data is **real market data driving a paper-trading simulator** (live KuCoin
public quotes, simulated fills, no exchange keys — by design), pipeline
healthy, but with the defects listed below. This branch fixes all confirmed
defects and rebuilds the public UI.

Live service topology (unchanged by this pack):
- `trader-autopilot` (KeepAlive) → 10s tick loop, state in `~/Sandbox/grokbot/autopilot/`
- `trader-dashboard` 127.0.0.1:8875 (`-m paper_grid.server --read-only`)
- `trader-publisher` every 300s → `vercel deploy --prod`
- tailnet: `https://dans-mac-studio.tailc56ca0.ts.net:8444` (fixed, see 2.7)

## 2. Fix log

### 2.1 Publisher hard-failed instead of truncating — FIXED
- **Symptom:** >20 closed bots or >5 watchlist entries → whole Vercel deploy
  failed → public site silently froze at last snapshot.
- **Files:** `paper_grid/public_autopilot.py` (truncate newest-20 / top-5,
  emit `truncated` note), `paper_grid/publish_vercel.py` (unchanged behavior,
  receives no more ValueError from sanitizer).
- **Verification:** `test_overflow_truncates_newest_closed_bots_and_watchlist_with_note`
  + `test_untruncated_payload_reports_zero_counts` pass; synthetic 25-bot
  payload → exactly 20 kept (newest by closed_ms), `truncated` note correct.

### 2.2 `change_24h_pct` ≡ `change_7d_pct` on young accounts — FIXED
- **Symptom:** both KPI cards showed the same number when the equity curve had
  no sample older than the window (fallback-to-start bug in both).
- **Files:** `trader/autopilot/policy.py` (return None when window
  unsatisfied); `paper_grid/public_autopilot.py` (null passthrough verified);
  `paper_grid/paper.html` renders null as "—%".
- **Verification:** `test_window_changes_are_none_without_an_older_equity_sample`
  passes (young account → both None; 24h sample → distinct 24h value, 7d None).

### 2.3 Misleading `/api/health` on read-only dashboard — FIXED
- **Symptom:** `--read-only` reports `worker_alive:false` and describes the
  zmarty experiment; any external monitor false-alarms.
- **Files:** `paper_grid/server.py` (file-derived health in read-only mode:
  `source: trader-autopilot-file`, liveness from snapshot heartbeat age <90s,
  `tick_age_s` verbatim).
- **Verification:** `test_read_only_health_comes_from_autopilot_snapshot` passes
  (missing file → alive:false; fresh heartbeat → alive:true + tick_age_s 3.5;
  120s-old heartbeat → alive:false). Live curl after restart: parent lane.

### 2.4 Local errors masqueraded as "KuCoin down" — FIXED
- **Symptom:** policy/engine exceptions flipped `kucoin_ok=False`, pausing
  decisions and looking like an exchange outage.
- **Files:** `trader/autopilot/runtime.py` (split handling; local errors →
  ERROR code 5 `LOCAL_ERROR`, `kucoin_ok` untouched).
- **Verification:** `test_local_policy_error_keeps_kucoin_ok_and_logs_code_5`
  passes (kucoin_ok stays true, ERROR code 5 in events.jsonl, exception text
  never persisted). Note: events.jsonl schema (`validate_event`) forbids string
  values, so the short message is emitted as a stderr log line with only the
  exception type; the event itself carries `type: ERROR, code: 5`.

### 2.5 `/api/events` re-parsed all files per request — FIXED
- **Files:** `trader/autopilot/storage.py` (`read_events` mtime+size keyed
  per-file parse cache, bounded at 128 entries; same response shape and cursor
  semantics). No `storage.py` exists under `paper_grid/` — the events reader
  lives in the trader storage module the server imports.
- **Verification:** `test_event_reader_caches_parsed_files_until_they_change`
  passes (unchanged file → zero re-reads; appended file → invalidated).

### 2.6 Public UI rebuilt (paper.html) — REBUILT
- Wasted 404 per load on Vercel (origin sniff → `/data/*` first off-localhost;
  sessionStorage memo for local mode).
- Double render per poll; O(n²) event dedupe (now Map-keyed).
- BUY/SELL empty-state copy now says the fill may be outside the published
  feed window (was: flat "No fill").
- Design: dense pro desk theme — dark surfaces, tabular numerals, positions
  as sortable table + expandable detail (ROE%, range bar, sparkline, ladder,
  liquidation), rebuilt equity chart (drawdown shading, crosshair, running-max
  baseline), compact activity/history tables. All disclosures kept. Still
  single-file, zero external requests.
- **Verification:** HTMLParser clean; `node --check` on the 42KB script block
  PASS; zero external src/href URLs; VM behavior fixture passes;
  `test_paper_page` 4/4; `npm run verify` exit 0. Live: next publisher cycle
  deployed it — live site sha256 == local `paper.html` sha256 (`99f09b1e…`);
  hover/expand/sort are delegation-based and warrant one human glance in a real
  browser.

### 2.7 Tailnet dashboard proxy — FIXED (ops, no code)
- `tailscale serve --https=8444 http://127.0.0.1:8875` registered; verified
  from Mac and from dexter (live `/api/autopilot`, tick_age_s=0).

## 3. Verification record (fill in)

- [x] `npm run verify` — all suites (check.mjs ✔, node tests ✔, paper 325 ✔, trader 279 ✔; exit 0)
- [x] `python3 -m pytest trader/tests -q` — 279 passed, 139 subtests passed
- [x] Sanitizer truncation: synthetic 25-closed-bot payload → 20, newest kept
- [x] `curl :8875/api/health` (read-only) → new shape confirmed live:
      `source: trader-autopilot-file`, `worker_alive: true`, `tick_age_s: 0`
- [x] Restarted `trader-autopilot` + `trader-dashboard` → `/api/autopilot`
      `tick_age_s: 0`, equity updating, 24h/7d `None` (new null semantics)
- [x] Publisher cycles post-change → consecutive `status: published`;
      `truncated` note live in `/data/autopilot.json` (`{'closed_bots': 0, 'watchlist': 0}`)
- [x] Vercel site: new UI live (hash-identical to repo `paper.html`);
      static snapshot 3 min old at check time; 404-avoidance logic in place
- [x] 24h/7d KPIs → both `None` → rendered "—%" on young account (correct per 2.2)

### 3.1 Final acceptance checklist

- [x] Merge → confirm publisher cycle after merge deploys clean.
- [x] Human glance: position-row expand + chart hover on the live site.

## 4. Known non-goals / follow-ups for Codex

- **Candlestick charts** need OHLC exposure to the frontend (new API route
  from sqlite klines) — intentionally out of scope here.
- `/api/*` remains local-only by design; Vercel serves static snapshots.
- The dormant `.github/workflows/vault-sync.yml` in DansLab-Vault is
  unrelated to this repo (see vault `Operations/2026-09-12 Brain Repair.md`).
- `~/Sandbox/grokbot/danslabtrader-stage-13khzxwl/` is an inert old deploy —
  safe to delete, not part of any pipeline.

_Last updated: 2026-09-12 (Kimi lane)_
