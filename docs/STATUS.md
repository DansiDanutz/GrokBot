# GrokBot — status (single source of truth)

Updated 2026-09-11. Phase C source is awaiting Claude's final gate; no operator
cutover has been performed by Codex.

## What GrokBot is now

GrokBot is an hourly KuCoin grid radar and a paper-only autopilot. A scored core
and bench explain which coins qualify. Only core coins can open simulated bots:
five total, preferred neutral/long/short slots, with borrowing up to four of one
direction. Public market ticks update the paper positions; no exchange order is
placed. Dan enters any real bot in KuCoin himself.

The new `/paper` page is the site landing page, with watchlist reasons, bots by
direction, equity, events and freshness. `/radar` retains the hourly radar;
`/control` retains the separate v1 rebound dashboard. A published page is a
snapshot; the local API provides ten-second updates.

## Done and pending

| Piece | Source status | Location |
|---|---|---|
| Public market data and hourly radar | Merged default | `trader/data/`, `trader/radar/` |
| Phase A paper accounting | PR #22 merged after PASS | `trader/papergrid/` |
| Phase B daemon, shared scores, core/bench and Telegram | PR #23 merged at `8df06c2` after PASS on `fe1c5e6` | `trader/autopilot/` |
| Phase C public DTO/API, paper page, publication routes | Implemented; final gate pending | `paper_grid/` |
| Three LaunchAgent examples | Source only; not installed by Codex | `config/launchd/` |
| Historical research | Superseded by lean radar; no new runs | Retained reference branches |
| Operator cutover | Dan-only, pending final gate | RUNBOOK below |

Source gates prove offline behavior. They do not establish deployment health or
live trading performance. The existing v1 worker, collector and installed agents
were not modified or restarted. Issue #16 stays open until the final gate and
handoff; Phase C remains unmerged during its audit.

## RUNBOOK — Dan runs these after the final gate

These commands change installed services and can publish the site or send
Telegram messages. They are instructions, not actions Codex performed. They
assume the merged default checkout at `/Users/davidai/ZCodeProject/GrokBot`, the
existing market database and existing private Telegram/Vercel credentials. Do not
put tokens into a plist, command argument or repository file.

### 1. Prepare and preserve rollback

```sh
cd /Users/davidai/ZCodeProject/GrokBot
git switch codex/mac-studio-foundation
git pull --ff-only
npm run verify
npm run verify:secrets
mkdir -p ~/Sandbox/grokbot/radar ~/Sandbox/grokbot/autopilot ~/Sandbox/grokbot/vercel-publisher
chmod 700 ~/Sandbox/grokbot/autopilot
cp -p ~/Library/LaunchAgents/com.danslab.trader-publisher.plist ~/Sandbox/grokbot/vercel-publisher/publisher-before-phase-c.plist
launchctl bootout gui/$(id -u)/com.danslab.trader-publisher
```

Stop only the publisher. Keep the v1 control worker and collector running. Preserve
`~/Sandbox/grokbot/vercel-publisher/config.json` and its `auth/` directory in place;
the new publisher reuses that project identity/authentication. The backup preserves
the old publisher command for rollback. If the expected installed publisher plist
is absent, stop and locate the actual existing plist before any replacement.

### 2. Install the three examples and insert your chat id

```sh
cp config/launchd/com.danslab.trader-radar.plist.example ~/Library/LaunchAgents/com.danslab.trader-radar.plist
cp config/launchd/com.danslab.trader-autopilot.plist.example ~/Library/LaunchAgents/com.danslab.trader-autopilot.plist
cp config/launchd/com.danslab.trader-publisher.plist.example ~/Library/LaunchAgents/com.danslab.trader-publisher.plist
open -e ~/Library/LaunchAgents/com.danslab.trader-radar.plist
open -e ~/Library/LaunchAgents/com.danslab.trader-autopilot.plist
```

Replace `REPLACE_WITH_DAN_CHAT_ID` in the two opened files. Check that
`/opt/homebrew/bin/python3` and the checkout paths match your machine. The wrapper
reads the existing `~/.config/danslab/credentials/telegram.json`; it supplies the
Telegram environment variable without embedding a token in the plist. The
publisher also runs through this wrapper, while Vercel authentication continues
through its existing private CLI auth directory.

```sh
plutil -lint ~/Library/LaunchAgents/com.danslab.trader-radar.plist
plutil -lint ~/Library/LaunchAgents/com.danslab.trader-autopilot.plist
plutil -lint ~/Library/LaunchAgents/com.danslab.trader-publisher.plist
```

### 3. Verify the wrapped commands manually, then bootstrap

Run these from the checkout, using each installed plist's exact argument array.
The autopilot manual smoke run substitutes `once` for `run`. The radar and
autopilot manual commands send their first Telegram messages; the publisher
manual command deploys the staged site. Check each succeeds before continuing.

```sh
/opt/homebrew/bin/python3 - <<'PY'
import pathlib, plistlib, subprocess
root = pathlib.Path.home() / 'Library/LaunchAgents'
for name in ('radar', 'autopilot', 'publisher'):
    config = plistlib.loads((root / f'com.danslab.trader-{name}.plist').read_bytes())
    args = list(config['ProgramArguments'])
    if 'REPLACE_WITH_DAN_CHAT_ID' in args:
        raise SystemExit('Set your chat id first')
    if name == 'autopilot':
        args[args.index('trader.autopilot') + 1] = 'once'
    subprocess.run(args, cwd=config['WorkingDirectory'], check=True)
PY
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.danslab.trader-radar.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.danslab.trader-autopilot.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.danslab.trader-publisher.plist
```

If radar/autopilot are already registered, boot out those exact labels before
bootstrapping their replacements; never launch a second instance. Radar runs at
minute :05 hourly. Autopilot is one KeepAlive daemon with ten-second ticks;
KeepAlive can start it on bootstrap despite RunAtLoad=false. Publisher runs every
300 seconds. The state lock prevents concurrent autopilot instances.

### 4. Start the separate read-only local dashboard

Use a new terminal on port 8875 so the existing v1 server at 8873 stays untouched:

```sh
cd /Users/davidai/ZCodeProject/GrokBot
paper_tailnet_host=$(tailscale status --json | /opt/homebrew/bin/python3 -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))')
/opt/homebrew/bin/python3 -m paper_grid.server --read-only --port 8875 --tailnet-host "$paper_tailnet_host" \
  --runtime /Users/davidai/Sandbox/grokbot/zmarty-paper-runtime \
  --radar-snapshot /Users/davidai/Sandbox/grokbot/radar/radar.json \
  --autopilot-snapshot /Users/davidai/Sandbox/grokbot/autopilot/autopilot.json \
  --events-dir /Users/davidai/Sandbox/grokbot/autopilot
```

The exact tailnet hostname is explicitly allowed for HTTP Host validation; other
hostnames remain rejected. Tailscale preserves the incoming Host when proxying
TCP-backed HTTP ([proxy implementation](https://github.com/tailscale/tailscale/blob/main/ipn/ipnlocal/serve.go)).
Keep this terminal running. `--read-only` disables the old experiment scheduler;
it does not start a second control worker. Binding stays 127.0.0.1. In another
terminal, expose that loopback service to your tailnet:

```sh
tailscale serve --bg http://127.0.0.1:8875
tailscale serve status
```

Use the tailnet HTTPS URL printed by Tailscale; this is Serve, not public Funnel.
See the [official Serve command reference](https://tailscale.com/docs/reference/tailscale-cli/serve).

### 5. Verify publication, freshness and logs

```sh
curl -fsS http://127.0.0.1:8875/api/autopilot
curl -fsS 'http://127.0.0.1:8875/api/events?since=0'
curl -fsSL https://danslabtrader.vercel.app/ -o /tmp/danslab-paper-home.html
curl -fsSL https://danslabtrader.vercel.app/radar -o /tmp/danslab-radar.html
curl -fsSL https://danslabtrader.vercel.app/paper -o /tmp/danslab-paper.html
curl -fsSL https://danslabtrader.vercel.app/control -o /tmp/danslab-control.html
cmp /tmp/danslab-paper-home.html /tmp/danslab-paper.html
curl -fsSL https://danslabtrader.vercel.app/data/autopilot.json
launchctl print gui/$(id -u)/com.danslab.trader-radar
launchctl print gui/$(id -u)/com.danslab.trader-autopilot
launchctl print gui/$(id -u)/com.danslab.trader-publisher
tail -n 30 ~/Sandbox/grokbot/radar/radar.stdout.log ~/Sandbox/grokbot/radar/radar.stderr.log
tail -n 30 ~/Sandbox/grokbot/autopilot/autopilot.stdout.log ~/Sandbox/grokbot/autopilot/autopilot.stderr.log
tail -n 30 ~/Sandbox/grokbot/vercel-publisher/publisher.stdout.log ~/Sandbox/grokbot/vercel-publisher/publisher.stderr.log
```

Open the pages as well: verify core/bench reasons, direction totals and no browser
CSP errors. Local LIVE means tick age <30s, DELAYED <180s, STALE otherwise. A
Vercel page must say snapshot/published age, not LIVE. Check snapshot timestamps
advance and publisher.json reports a recent successful publication. A quiet
stdout alone is not proof that the daemon is healthy.

### Rollback publication

```sh
launchctl bootout gui/$(id -u)/com.danslab.trader-publisher
cp -p ~/Sandbox/grokbot/vercel-publisher/publisher-before-phase-c.plist ~/Library/LaunchAgents/com.danslab.trader-publisher.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.danslab.trader-publisher.plist
launchctl kickstart gui/$(id -u)/com.danslab.trader-publisher
```

The public site changes back after the restored publisher succeeds; bootstrapping
alone does not undo an already-published deployment. This rollback changes only
the publisher. If you also want to stop the paper autopilot, boot out its exact
label separately. Do not delete its state, the market DB or the control runtime.

## Boundaries

No real orders, trade-enabled exchange keys, paid calls or secrets in repository
files/plists. No research backtests/replays. No operator action above is executed
by the development or verification suites.
