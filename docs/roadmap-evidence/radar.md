# Lean KuCoin radar handoff

Date: 11 September 2026. Status: source complete for audit at the fixed, unmerged PR head. Base: `07af3ae` on `codex/roadmap-phase-2`. Branch: `codex/lean-kucoin-radar`. Tracking: [issue #16](https://github.com/DansiDanutz/GrokBot/issues/16).

## Commit ledger

| Deliverable | Commit | Evidence |
| --- | --- | --- |
| Radar engine and CLI | `86b0ece` | SQLite fixture; direction, ranges, constants, gates, ranking, atomic JSON and tables |
| Local and public `/radar` | `f5a08d0` | nonce CSP, read-only API, allowlisted public DTO and static CSP route |
| Hourly template | `6a4339e` | parsed plist; exact database/output paths; minute `05`; not installed |
| Telegram | `1b8d500` | top-three identity digest, suppression, 0600 state, private wrapper, sanitized failures; no send |
| Operator guide | `4c013e5` | command, fields, sections, constants and manual-first procedure |
| Handoff | this commit | real read-only run, limits and final gates |

RED was observed before each behavior slice: missing `trader.radar`, absent `/radar` and exported page, absent plist, and absent Telegram module. Final gate count is **38 Node + 291 paper Python + 112 trader Python = 441 tests**. Every commit ran `npm run verify` and `npm run verify:secrets` against its staged tree. No dependency was added.

## Real database run

Command (no Telegram arguments):

```sh
python3 -m trader.radar --database ~/Sandbox/grokbot/market-data/phase-2-20260911/market.sqlite3 --json ~/Sandbox/grokbot/radar/radar.json
```

At **2026-09-11 17:36:28 Europe/Bucharest**, it read the database in SQLite `mode=ro`, analysed **359** symbols, and found **48** passing the three liquidity gates. The JSON was 287,983 bytes. The complete section output was:

```text
MAJORS READ: XBTUSDTM NEUTRAL; ETHUSDTM LONG; SOLUSDTM NEUTRAL
TURNING UP: NESUSDTM 12.50 grids/h, 0.11476..0.17139, 50 grids
TURNING DOWN:
  BCHUSDTM 3.09; PENGUUSDTM 3.66; EDENUSDTM 3.64; FARTCOINUSDTM 4.23
  AVNTUSDTM 4.05; GIGGLEUSDTM 3.50; ASTERUSDTM 3.35 grids/h
LONG:
  NIULAIUSDTM 22.61, 0.11221..0.18965, 66 grids
  METUSDTM 10.91, 0.2356..0.2909, 26 grids
  NEARUSDTM 6.16; EIGENUSDTM 5.93; FFUSDTM 4.15; HUSDTM 4.69
  ETHUSDTM 0.94; POWERUSDTM 2.15 grids/h
SHORT:
  BEATUSDTM 7.80; CLOUSDTM 5.50; MYXUSDTM 3.96; 1000BONKUSDTM 3.47 grids/h
NEUTRAL:
  4USDTM 9.91, 0.01493..0.0357, 118 grids
  APTUSDTM 5.30; ENAUSDTM 4.55; DASHUSDTM 4.34; DOTUSDTM 5.64
  KASUSDTM 4.82; ADAUSDTM 3.58; PEPEUSDTM 3.53 grids/h
MOVERS:
  NIULAIUSDTM 22.61; 4USDTM 9.91; METUSDTM 10.91 grids/h
```

Rows within normal direction sections are ordered by expected grids/hour multiplied by the capped liquidity weight, so a row can precede another with a larger raw grids/hour value. Movers preserve the prototype's absolute 24-hour-move ordering.

## Boundaries and remaining operator work

- No exchange order, paid call, trade credential, backtest, replay, sweep, preregistration, or fitting code was used. The old `codex/grid-kucoin-v3` work remains unmerged as an archive.
- No v1 runtime, existing LaunchAgent, collector, paper engine, Telegram bot, or publisher process was changed or restarted. The ongoing market collector/backfill were only read through SQLite and remain owned by their existing process.
- The new LaunchAgent is only an `.example`. Dan must replace the chat placeholder and run the wrapped command manually before any installation. This handoff did not read a credential or send Telegram.
- This branch adds **under 800 lines** across code, tests, templates and documentation. The radar is a heuristic operator recommendation, not an order path or guaranteed fill estimate.
- Claude should audit the exact PR head. Codex must remain idle after reporting it.
