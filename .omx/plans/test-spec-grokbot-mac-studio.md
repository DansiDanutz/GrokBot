# Verification plan

1. Node syntax/configuration and unit tests: model pin, accepted effort, port/concurrency limits, credential/tunnel stripping, idempotent private configuration.
2. MCP actual subprocess: initialize, list, call, notifications, malformed and oversized inputs, forbidden args, fixed loopback targets, bounded TCP probes.
3. Upstream pinned OSS export: installation, typecheck/build, relevant Codex/config/default-model/MCP tests using upstream temporary-home fixtures.
4. Offline upstream fixture: launch via control-omb, create fake-engine bot, send local fixture input, wait for settled, inspect response, stop owned process.
5. Configured local startup: own PID at health endpoint, edition oss, selected Astra/default, catalog availability, live UI inspection. No paid generation.
6. Final verification: root gate rerun after edits, Git diff check, global Codex version unchanged, no secret/runtime files staged.
