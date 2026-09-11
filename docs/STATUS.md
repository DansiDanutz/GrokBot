# GrokBot — status (single source of truth)

_Updated 2026-09-11 17:55 Europe/Bucharest by Claude. Update this file whenever the state changes; do not keep status in chat._

## What GrokBot is now
A lean hourly **KuCoin grid radar**: reads the local KuCoin candle database, labels every liquid USDT perp LONG / SHORT / TURNING-UP / TURNING-DOWN / NEUTRAL, proposes grid range, step, grid count and expected grids per hour, and shows it on the dashboard and in Telegram. Dan sets the bots by hand in KuCoin. **No orders are ever placed.** Everything else (rebound paper routine, grid backtests, replay research) is archived.

## Done
| Piece | State | Where |
|---|---|---|
| Market data collector + SQLite (522 contracts, 1m/1h candles, funding, book) | running, Phase 2 code | `trader/data/`, DB `~/Sandbox/grokbot/market-data/phase-2-20260911/market.sqlite3` |
| Radar engine + CLI | merged (PR #17, audited PASS) | `trader/radar/` |
| Dashboard page `/radar` + public `/radar` export | merged (PR #17) | `paper_grid/radar.html`, `server.py`, `public_snapshot.py` |
| Telegram delivery (top 3 per section, only on change) | merged (PR #17) | `trader/radar/telegram.py` |
| Hourly LaunchAgent template | merged, **not installed** | `config/launchd/com.danslab.trader-radar.plist.example` |
| Operator guide | merged | `docs/radar.md` |
| Calibration constants (k 1.9 small caps / 0.45 majors, step 0.8 % / 0.52 %) | in code, fitted on Dan's live bots HEMI/BTR/MOVR/SOL | `trader/radar/radar.py` |

## In flight
| Item | Owner | State |
|---|---|---|
| PR #18: price + range in Telegram, stale-snapshot guard, EMA note | Codex | merged after Claude PASS at `8b43d51` |
| PR #20: radar dashboard design | Claude | merged at `51aa130` |
| PR #21: autopilot specification | Claude | merged at `f814172`; single source of truth for Phases A–C |
| PR #19: phase-2, radar, status and autopilot specification into default | Codex → Claude audit | open; Phase 0 handoff pending |

## Remaining to call it finished
1. **Audit + merge PR #18** (Claude, then Codex merges).
2. **Merge `codex/roadmap-phase-2` into the default branch `codex/mac-studio-foundation`.** The radar and the data layer live only on the phase-2 branch (25 commits ahead). Without this merge the default branch has no radar.
3. **Install on the Mac (Dan):** copy the plist template, insert chat id, run the wrapped command once by hand (sends the first message), then `launchctl bootstrap` it. Hourly at :05 from then on.
4. **Archive the research:** close issues #12 and #13, close PRs #14 and #15 unmerged, mark `codex/grid-kucoin-v3` archived, replace `docs/pro-trader-roadmap.md` Phases 3–8 with a pointer to this file.
5. **Decide the v1 rebound paper run:** stop it at its first 48 h audit (2026-09-13 02:11) or keep it as a control. It is unrelated to the radar.

## Not part of GrokBot any more
Paperclip board hygiene, Xlaude/David issues, fleet secrets, disk cleanup — tracked elsewhere.

## Boundaries that stay true
No exchange keys with trade permission, no paid API calls, no secrets in files or plists, no backtests or replays unless Dan asks for them explicitly.
