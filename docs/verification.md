# Verification record — 2026-09-11

Environment: macOS ARM64, Node 26.8.2, 36 GiB RAM. The upstream pin is `1fcefafc7e31bd6b271be36fc3b80311e43d95b8` (package version 0.1.71).

| Check | Result | Evidence |
|---|---|---|
| Integration syntax/config checks | PASS | `npm run verify` |
| Integration unit/contract/lifecycle tests | PASS | 17 passed, 0 failed |
| Upstream source lint | PASS | oxlint, warnings denied; explicit pinned source list |
| Upstream type checks | PASS | `tsc -b && tsc -p tsconfig.server.json` |
| Upstream selected regression tests | PASS | 7 files, 212 passed, 0 failed |
| Upstream production UI build | PASS | 2,705 modules; build completed in 14.68 s on successful retry |
| OSS-only startup | PASS | `/api/edition` returned `edition: oss`, no features |
| Local app readiness | PASS | `/api/health` returned upstream app identity and the spawned server's PID |
| Codex installation/authentication | PASS | project CLI 0.154.0; ChatGPT subscription authenticated |
| Astra model availability | PASS | live app `/api/instances` included `gpt-6-astra`; rendered picker showed GPT-6-Astra / Low |
| Fleet MCP registration | PASS | application connection test returned both `danslab_service_status` and `danslab_machine_info` |
| Local service observation | PASS | six fixed endpoints TCP reachable; not a health assertion |
| Offline chat workflow | PASS | disposable fixture: create bot, send, wait `settled`, response `hello from fake claude` |
| Browser inspection | PASS | chat, GrokBot profile, saved standing instructions, Astra picker and composer visible |
| Global Codex preservation | PASS | global CLI remains 0.149.1; project CLI is 0.154.0 |

The offline fixture used only the repository's fake model CLI. Its token/cost values are synthetic, not actual spend. It was stopped via its owning launcher after verification.

## Issues encountered and resolved

- Global pnpm failed to switch to upstream's required 10.33.0. The integration now installs that exact version locally and calls its executable directly.
- The first frontend build received SIGTERM. A retry with a 1 GiB Node heap succeeded; setup now defaults to that bound. The cause of the original signal was not established.
- One catalog test inherited this session's `CODEX_HOME`, bypassing the fixture home. Removing the variable only from the verification child fixed isolation; the complete selected suite then passed. The verification script records this environment boundary.
- Plain upstream `oxlint .` found no files because the integration repository ignores `.runtime/`. The verification wrapper enumerates pinned tracked source and bypasses parent ignores while retaining upstream's vendor/generated exclusions. It does not suppress lint findings.
- Launcher review found masked failure codes, an imprecise startup timeout, and no hung-child escalation. Regression tests now cover preserved failures, the 45-second deadline, and owned-process-group termination with an eight-second grace period.
- Port 8799 was already occupied by an existing service. GrokBot uses 8871/8872; that service was not stopped or changed.

## Reproduce

```sh
npm ci --ignore-scripts
npm run setup
npm run verify
npm run verify:upstream
npm run doctor
npm start
```

For a chat fixture, follow upstream `docs/verification/README.md` and `chat-turns.md`. Launch `scripts/control-omb.ts launch`, use its printed URL explicitly, and stop that same launcher. Never send a fixture prompt to the user's actual account.

## Limits

- No live Astra generation was submitted. Availability/authentication does not establish output quality, latency, or quota capacity for real work.
- The full upstream suite, Electron packaging, native computer control, voice, cloud VMs, mobile pairing, and paid providers were not tested.
- The upstream build reports large JavaScript chunks. No upstream UI optimization was attempted.
- Fleet integration is read-only status. No dispatch, routing-oracle decisions, Telegram polling, service restarts or droplet changes were performed.
- The existing global Codex configuration still applies to the signed-in CLI where upstream preserves it. Separate app state is not a security boundary against every file readable by the user's account.
- Runtime UI branding stays OpenMausBot. No enterprise branding or budget entitlement is bypassed; there is no enforced dollar spending cap in this OSS integration.

## Paper research consolidation — 11 September 2026

The completed `paper_grid/` source was imported byte-for-byte from local ZmartyChat
implementation commit `6ee309c2134ad88e63c0cd3c021859a6c02df03b`; all 30 hashes match
`config/paper-source.lock.json`. This is a repository consolidation, not a move
of the active runtime.

- `npm run verify`: syntax/configuration checks, 17 Node tests and 159 Python tests passed.
- Local and public dashboard inline JavaScript passed `node --check`.
- Both placeholder LaunchAgent plists parsed successfully.
- `npm run verify:secrets` scans the committed revision before push and in CI.
- No provider calls, state migration, service restart or new deployment was needed for this import.
- The source deployment's public URL, first background publication and browser checks are recorded in `paper_grid/PUBLIC_WEB.md`.

A new installation from GrokBot and multiple days of unattended publishing have
not been exercised by this source-copy verification. Refer to `docs/paper-operations.md`
for the still-active service paths and explicit migration precautions.

## Learning lab — 11 September 2026

`npm run verify` passed 17 Node and 181 Python tests. Analytics regression coverage
includes deduplicated archives, missing evidence, lifecycle reconstruction,
period boundaries, cost accounting, privacy, independent accounts and bounded
output. The two dashboards passed JavaScript syntax and browser checks against
real empty data and isolated synthetic win/loss fixtures. Deployment evidence is
in `paper_grid/ANALYTICS.md`.
