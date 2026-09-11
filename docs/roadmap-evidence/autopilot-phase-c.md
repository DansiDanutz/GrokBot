# Autopilot Phase C — frozen audit handoff

Base: `8df06c26c275232f1623fe1c2d88af2e786c88d2`, default branch after merging
PR #23 unchanged from gated head `fe1c5e6d94d4bdc65ecd8edee286e00cabf00c0d`.
Gate: issue #16 comment 5637610775. Both post-merge gates passed (539 tests).
Branch: `codex/autopilot-phase-c`, targeting `codex/mac-studio-foundation`.

Implementation and RUNBOOK: `10cd83789389a865ebb22d6f79aa88864b4a9541`.
This evidence commit is the final head reported on #16. Leave the Phase C PR
unmerged and await Claude's final gate.

## Delivered

- New `/paper` page and root landing: core/bench score cards, all eleven reason
  templates, last 24 watchlist events, open/closed bots grouped by direction,
  per-direction totals, grids/hour, range markers, PnL sparklines, equity/change
  figures and equity curve, last 50 live events and freshness status.
- DOM APIs and textContent only; one style and one script, no external resources
  or inline style attributes. Bot rows retain identity across updates. Local
  snapshots/events poll every ten seconds. LIVE <30s, DELAYED <180s, STALE otherwise;
  cached snapshot age advances rather than resetting freshness on each fetch.
  Static fallback explicitly says snapshot/published age.
- `_autopilot` public projection delegates to `public_autopilot.safe`: fixed enums,
  finite numeric fields, bounded bot/watchlist/history/curve lists, unknown private
  keys removed. The local API and publisher share this projection.
- Local `/api/autopilot` and `/api/events`, loopback binding, read-only methods,
  nonce CSP, all-parent symlink checks and bounded input reads. Events return at
  most 500 rows. An optional `after_event_id` cursor breaks timestamp ties so
  500+ events at one timestamp do not block the next batch; old timestamp-only
  calls preserve their prior semantics.
- `/`, `/paper`, `/radar` and `/control` publication with separate hash CSP routes.
  Publisher stamps `published_at_ms`, stages sanitized radar/autopilot JSON, and
  reads the existing control report without modifying that runtime. Missing
  default snapshots publish an unavailable DTO; explicit missing/invalid snapshot
  paths fail before deployment.
- Three operator examples: existing hourly radar retained, new KeepAlive autopilot,
  publisher changed to five minutes through credential-exec. None installed.
- STATUS/RUNBOOK replaces stale completion status and documents cutover, manual
  wrapped smoke runs, bootstrap, live-site curl/log checks and publisher rollback.
  A separate `--read-only` server on 8875 leaves the v1 server at 8873 untouched.
  Exact `--tailnet-host` is opt-in and requires read-only mode; other Host values
  and forwarded-header spoofing remain rejected. Tailscale's published proxy
  implementation preserves incoming Host, which motivated this tested option.

## Verification

**563 tests = 38 Node + 315 paper Python + 210 trader Python**.
`npm run verify` and `npm run verify:secrets` passed before each commit.
There are **24 new Python test cases**: six DTO, eight server, three plist,
two page harness cases and five publication cases. Page tests additionally execute
an offline Node DOM harness with behavioral assertions. Existing SQLite
ResourceWarnings remain non-failing and are inherited from earlier phases.

RED evidence was observed before implementation for absent DTO/routes/flags,
missing daemon template and old publisher interval, new landing export inputs,
and page behavior. Review regressions reproduced the same-timestamp event cursor
stall, cached-snapshot freshness reset and tailnet Host rejection before fixes.
All focused and full suites are green. A secret-gate staging precondition was
resolved by staging the intended STATUS file; no scanner rule was weakened.

Assertions cover: allowed public fields and forbidden free text, finite values,
bounds/downsampling, symlink rejection, 500-event pagination including 510 events
sharing a timestamp, malformed cursor rejection, nonce/static CSP routes, root
and paper content equality, preserved control/report data, readonly source files,
read-only startup without a monitor/caffeinate, exact tailnet Host and loopback
binding, unavailable/static fallback, unchanged bot-node identity, reason text
injection safety, freshness boundaries, event retention/deduplication and plist
argument/schedule consistency.

## Visual and manual checks

A temporary server on 127.0.0.1:8897 served only synthetic snapshots under a
canonical temporary path. No control worker, collector or autopilot was started.
Compared the page against the existing radar design in the browser; desktop and
390px mobile cards are readable with no page-wide overflow and no console errors.
Visual review opened watchlist history by default and added the sideways-scroll
hint for intentionally wide bot tables. Final visual-verdict: **93 / pass**.
The temporary server and browser tabs were closed afterward; installed services
were not touched. All price/PnL values used in that visual preview were synthetic.

## Limits and remaining operator work

No Vercel deployment, real market/API request, Telegram send, installed-agent
change, private credential read, v1-runtime write, replay or backtest occurred.
Official public Tailscale documentation/source was read for the RUNBOOK. Actual
Tailscale routing, publisher credentials and installed-service operation remain
Dan's post-gate verification; source tests do not establish those outcomes.

Event reads retain the existing 2 MiB per-day file cap and fail closed beyond it.
The browser feed is bounded to 50 events; the publisher is periodic, not real-time.
Live status belongs to the local API; Vercel always shows snapshot age.

After the final gate, merge only as authorized, then report default head, test
count, these three example paths and Dan's manual steps before closing #16:

- `config/launchd/com.danslab.trader-radar.plist.example`
- `config/launchd/com.danslab.trader-autopilot.plist.example`
- `config/launchd/com.danslab.trader-publisher.plist.example`

Until then, port 8873 and the public deployment remain the existing control view.
The implementation is reviewable source; it has not been cut over.

## Conditional gate C-1 correction

Applied issue #16 comments 5638003293 and 5638035811. This correction changes only
committed documentation and the market-data plist example; no updater code or
installed service is changed. The new frozen head identifies this correction.

The collector example now names `com.danslab.trader-market-data`, uses the real
GrokBot checkout and `phase-2-20260911/market.sqlite3`, and runs the existing
`--duration-hours 24` command under KeepAlive with ThrottleInterval 10 and
RunAtLoad false. Logs remain under `market-data/logs/`; no credentials are needed.

STATUS lists four service examples. RUNBOOK step 0 checks the exact hand-started
PID 73444 before documenting its termination, waits for exit, installs/bootstrap
instructions for the collector, and polls a read-only MAX(time_ms) query for an
advancing ticker timestamp no older than five minutes before radar startup.
Step 5 repeats the data freshness check and adds collector status/log commands.
Publisher rollback explicitly leaves the collector safely running.

An offline assertion first failed on the old label/template, then passed for the
exact command, paths, KeepAlive/ThrottleInterval, log paths and RUNBOOK ordering.
Both full gates pass unchanged at **563 tests (38 + 315 + 210)**, secrets clean.
C-2 remains unchanged: the publisher wrapper is already explained in the RUNBOOK.
No PID was signalled, no installed plist was read or written, no database was
opened, and no agent was bootstrapped. Await Claude's re-gate before any merge.
