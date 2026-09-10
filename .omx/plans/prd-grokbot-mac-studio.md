# GrokBot Mac Studio foundation

User request: research GitHub Grok Bot alternatives, use the best configured base, and adapt it to the Mac Studio. Preserve the earlier GPT-6 Astra target.

Evidence selects OpenMausBot OSS, pinned at the successful-CI revision in upstream.lock.json. It uses the official Codex app-server and already handles Astra. Native local execution avoids expanding the current Docker VM.

Deliverable: reproducible local workspace with isolated data, project-local Codex, default Astra, bounded read-only fleet MCP, operational documentation and verification evidence.

Acceptance: local setup and build work; OSS edition confirmed; Astra appears in the account catalog; no model generation used for verification; root tests and relevant upstream checks pass; UI loads; offline fake-engine chat completes; no existing services or global tool versions changed.

Initial boundary: local workspace alongside the fleet. Autonomous fleet dispatch, live routing changes, global config edits, paid generation, proprietary application extraction and enterprise feature bypass are excluded. The user was asked whether a central fleet controller was intended; no answer was required for this independently useful local foundation.

Execution: own integration files and immutable upstream runtime export. No upstream source fork or global installations. Record limitations honestly, especially unverified live Astra turns and desktop control.
