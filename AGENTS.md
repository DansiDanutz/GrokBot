# GrokBot integration contract

This repository configures an unmodified OpenMausBot OSS runtime and contains
the separate continuous paper research system for Dan's Lab.
Machine rules: `~/AGENTS.md`, `~/CLAUDE.md`, `~/ZCodeProject/AGENTS.md`.
Issue: https://github.com/DansiDanutz/GrokBot/issues/1

- Own code lives in scripts/, tests/, config/, docs/, paper_grid/ and trader/. Never edit the upstream runtime.
- Pin upstream revisions in upstream.lock.json. Exclude enterprise/ when exporting OSS.
- Runtime, credentials, logs and machine snapshots stay in ignored .runtime/.
- Default engine is the project-local Codex CLI using its existing ChatGPT sign-in.
- Never overwrite global agent config or start another Telegram poller.
- Fleet tools are read-only, fixed-loopback probes; reachability is not health.
- Run npm run verify. For upstream changes follow its docs/verification/README.md.
- Use offline model fixtures for tests; do not submit generation requests automatically.
- Preserve licensing notices. UI branding remains upstream; enterprise entitlements are not bypassed.

_Last verified: 2026-09-11_

## Continuous paper research

- `paper_grid/` is independent of the OpenMausBot runtime and installed native Grok Bot.
- Keep private ledgers, provider keys, Vercel authentication and generated snapshots out of Git.
- Live zmarty services still use the original ZmartyChat-paper-grid checkout; see docs/paper-operations.md. The trader desk (autopilot, dashboard, publisher, radar, market-data) runs from `/Users/davidai/ZCodeProject/GrokBot-prod` (branch `design/polish`).
- Do not edit sealed source files in the active checkout or reset a running experiment during repository work.
- Keep paper-only execution, deterministic controls and separate publication. No live exchange orders.
- `npm run verify` includes the offline Python test suite; no provider requests or live credentials are needed.

## Public market data development

- `trader/data/` is a separate public-only SQLite data layer. All CLI database paths are explicit.
- Keep data, process manifests and public acceptance logs outside Git in the new Phase 2 workspace.
- `npm run verify` includes `test:trader`; all automated tests are offline.
- The market-data LaunchAgent is installed and running as
  `com.danslab.trader-market-data` (KeepAlive, since 2026-09-11); the
  `config/launchd/` template remains an example, not the live job.
