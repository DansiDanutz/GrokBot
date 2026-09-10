# GrokBot upstream selection

Research date: 2026-09-11. This is a source and maintenance assessment, not a claim that every candidate was installed or tested locally.

## Decision

Use **OpenMausBot's Apache-2.0 core as the upstream runtime**, with this repository holding separate DansLab configuration, integration code, verification, and operating instructions. Fetch an immutable source revision, verify its identity, and exclude `enterprise/` from the runtime export. Preserve upstream copyright, licensing, attribution, and third-party notices. Keep the upstream application identifiable as OpenMausBot; GrokBot is the DansLab integration project, not an implementation of an enterprise white-label feature.

This follows the upstream [licensing guidance](https://github.com/milind-soni/OpenMausBot/blob/fdc554a6a4b7ce800aea19086f8a202e5fddcf0e/LICENSING.md), which places customer-specific configuration, skills, packages, and connectors in the customer's own repository. That document also says the core runs without `enterprise/`; the upstream CI includes an OSS-only build check. The enterprise directory has separate terms and is not part of the selected runtime.

OpenMausBot fits the observed machine better than a new container-based platform: Apple Silicon, 36 GiB RAM, existing agent services, and a Docker VM currently limited to 2 GiB RAM and 2 CPUs. Its local chat path uses a Node harness and the already authenticated Codex CLI; it does not require a new PostgreSQL database or Docker computer. These hardware observations guide this decision only and are not a complete machine inventory.

The inspected local Codex CLI was **0.149.1**, signed in with ChatGPT. This is below the **0.153.1** Astra threshold encoded by the inspected upstream driver. That threshold is an upstream compatibility claim, not an independently established OpenAI guarantee. A compatible project-local CLI and a successful account model-catalog check are prerequisites before declaring Astra ready. Do not change the fleet's global CLI or infer model access merely from an app model label.

## Comparison

| Candidate | What the source establishes | Decision and remaining limitations |
| --- | --- | --- |
| [OpenMausBot](https://github.com/milind-soni/OpenMausBot) | Apache-2.0 core, native Apple Silicon desktop distribution, local official Codex app-server driver, dynamic model catalog, explicit Astra handling, tests covering drivers/API/desktop, active CI and releases. | **Selected.** Exclude separately licensed `enterprise/`. Local execution and Astra access still need verification. Optional paid connectors and cloud computers are unnecessary for the initial setup. |
| [Rakazo](https://github.com/elie222/rakazo) | Apache-2.0; beta; Pi model runtime with OpenAI-Codex subscription authentication and Responses SSE handling; unit, PostgreSQL integration, web E2E and Electron CI; macOS checks; ARM64 container images. | Strong runner-up for a server platform. PostgreSQL, Graphile Worker and Docker add services and resource needs to the current 2 GiB/2 CPU Docker VM. No load test was performed, so this is an operational fit concern, not a measured minimum-memory claim. |
| [gawkbot](https://github.com/najmuzzaman-mohammad/gawkbot) | Go/Bun workflow application; Codex, Hermes and OpenClaw runtime adapters; substantial tests and CI; local workspace data. | Best direct fleet-adapter coverage, but the Sustainable Use License restricts use/distribution. Its routine-authoring sidecar resolves Anthropic API, Claude CLI or Ollama, so selecting Codex in the main application does not establish an all-Astra path. |
| [CopilotKit/OpenBot](https://github.com/CopilotKit/OpenBot) | MIT alpha template; governed tool gateway and audit trail; substantial CI; LangGraph agent supports Responses. | Requires CopilotKit Intelligence configuration/project entitlement and a model credential. The default proof-of-concept bot uses Chat Completions and explicitly refuses GPT-6. More infrastructure and adaptation than this local subscription-based request needs. |
| [Guaca](https://github.com/madebywelch/guaca) | AGPL-3.0; macOS Tauri desktop; Codex app-server coding harness; direct subscription Responses implementation; Rust and frontend test files. | Promising but young, with a container backend and no GitHub Actions workflows found in the inspected tree. AGPL terms also differ from the selected permissive core. |
| [ashhart/OpenBot](https://github.com/ashhart/OpenBot) | MIT; Electron macOS ARM64 build configuration; local Codex app-server and other CLI adapters; shared skill discovery; native Mac tools. | Early 0.1.0 snapshot, no CI or test script found. Managed VM targets refuse supervised Codex; they require Pi or direct APIs. Too much verification work for the starting base. |
| [pftq/GrokBot](https://github.com/pftq/GrokBot) | MIT Windows desktop-control script supporting Grok and OpenAI API keys. | Wrong host platform; no tests or CI found, and the last observed code push was November 2025. |
| [grokbot-shim](https://github.com/codeaashu/grokbot-shim) | ISC shim with two test files, local Codex authentication support, Linux desktop/container setup. | Depends on runtime files extracted from an installed proprietary Grok Bot and undocumented integration points. Its ISC license does not license those extracted components. Poor reproducible Mac foundation. |
| [SmolVM](https://github.com/CelestoAI/SmolVM) | Apache-2.0 Python/Rust sandbox infrastructure, tests and CI, macOS QEMU support, Apple Silicon macOS desktop preview. | A possible later isolation component, not a complete bot workspace. Adds VM lifecycle, image downloads and storage overhead; the project declares alpha status. |

The search also surfaced [Anil-matcha/open-grok-bot](https://github.com/Anil-matcha/open-grok-bot) and [wolfqing/OpenGrokBot](https://github.com/wolfqing/OpenGrokBot). These received metadata-level screening only; no license was reported by GitHub for the former. Neither displaced the stronger candidates above. Popularity was a discovery signal, not the selection criterion.

## Evidence that matters for Astra and the fleet

OpenMausBot's inspected [`codex.ts`](https://github.com/milind-soni/OpenMausBot/blob/fdc554a6a4b7ce800aea19086f8a202e5fddcf0e/server/drivers/codex.ts) explicitly names `gpt-6-astra`, checks for a sufficiently new CLI, and drives the official `codex app-server` process. Its subscription path removes an inherited `OPENAI_API_KEY`. This favors the user's existing subscription without introducing a paid API fallback.

Its [`codex-catalog.ts`](https://github.com/milind-soni/OpenMausBot/blob/fdc554a6a4b7ce800aea19086f8a202e5fddcf0e/server/drivers/codex-catalog.ts) queries the installed CLI's paginated `model/list`. Official model selections explicitly use `modelProvider: "openai"`, preventing a pre-existing local-provider default from silently intercepting Astra. The static fallback catalog still defaults to Sol; it is not proof that Astra is available. The integration must explicitly select Astra and fail with a useful diagnosis if account discovery cannot establish access.

The upstream [`custom-engines.md`](https://github.com/milind-soni/OpenMausBot/blob/fdc554a6a4b7ce800aea19086f8a202e5fddcf0e/docs/custom-engines.md) documents ACP engines and OpenAI-compatible endpoints. **The latter currently support text and reasoning streams only, without tool calls.** Therefore pointing an instance at a Hermes or OpenClaw HTTP endpoint is not enough to claim full agent integration. Use bounded MCP tools or a verified ACP adapter for fleet operations, preserving the existing services and their configuration ownership. The upstream [MCP control plane](https://github.com/milind-soni/OpenMausBot/blob/fdc554a6a4b7ce800aea19086f8a202e5fddcf0e/docs/mcp-server.md) is useful for orchestration, but does not expose approval grants, credentials, deletion, or computer lifecycle.

The rejected gawkbot path is grounded in its [license](https://github.com/najmuzzaman-mohammad/gawkbot/blob/2000e28115491e33a3157e08b0823a3bd8c09cb2/LICENSE) and [`agent/src/serviceAuthor.ts`](https://github.com/najmuzzaman-mohammad/gawkbot/blob/2000e28115491e33a3157e08b0823a3bd8c09cb2/agent/src/serviceAuthor.ts). Main-provider configuration would leave a separate runtime to adapt. Its license permits internal business/personal use but restricts distribution to free noncommercial purposes and requires notices on modified copies.

Rakazo's alternative subscription path is visible in [`pi-oauth.ts`](https://github.com/elie222/rakazo/blob/670f57b9c7bf0d6ed4cc297ac2ceaa2ddadd90e2/packages/adapters/src/pi-oauth.ts) and its [Codex Responses transport tests](https://github.com/elie222/rakazo/blob/670f57b9c7bf0d6ed4cc297ac2ceaa2ddadd90e2/packages/adapters/src/pi-runtime-transport.test.ts). Its [self-hosting guide](https://github.com/elie222/rakazo/blob/670f57b9c7bf0d6ed4cc297ac2ceaa2ddadd90e2/docs/self-host.md) documents the additional stack and multi-architecture images.

## Revisions and verification limits

Source inspection used read-only GitHub API calls and shallow temporary clones. No candidate's install scripts, application code, tests, or model inference were executed during this comparative research. Test-file counts and CI definitions show verification investment; they do not prove local behavior or test quality by themselves.

| Project | Inspected revision | Observed upstream verification |
| --- | --- | --- |
| OpenMausBot | `fdc554a6a4b7ce800aea19086f8a202e5fddcf0e` | Head CI was pending at capture. The latest successful CI observed was [`1fcefafc7e31bd6b271be36fc3b80311e43d95b8`](https://github.com/milind-soni/OpenMausBot/actions/runs/34529505678). Latest published release was [v0.1.71](https://github.com/milind-soni/OpenMausBot/releases/tag/v0.1.71), with ARM64 DMG/ZIP assets. |
| Rakazo | `670f57b9c7bf0d6ed4cc297ac2ceaa2ddadd90e2` | [CI successful](https://github.com/elie222/rakazo/actions/runs/34517025384) at this revision. |
| gawkbot | `2000e28115491e33a3157e08b0823a3bd8c09cb2` | [Main CI successful](https://github.com/najmuzzaman-mohammad/gawkbot/actions/runs/34388009623); separate [release-drift check failed](https://github.com/najmuzzaman-mohammad/gawkbot/actions/runs/34508555123). |
| CopilotKit/OpenBot | `8819f1801baf496707ee68772cb3db6d2337d67d` | [CI successful](https://github.com/CopilotKit/OpenBot/actions/runs/34504675883). |
| Guaca | `c567e589a02793a363cf4a8733f4249a037e9fdf` | Source tests inspected; no Actions workflows found. |
| ashhart/OpenBot | `b544cb743986193fdc3d234ae66c8e44ef68fc00` | No test script or Actions workflows found. |
| pftq/GrokBot | `0e7eb599f81dc1bc0e3be110824d528df27282c7` | No tests or Actions workflows found. |
| grokbot-shim | `751181772df0371b241b18c1c699afeab6d68431` | Test scripts present; not run locally. |
| SmolVM | `6e68f6825cea1d65bdf57a99d99c406bf8a6e2e8` | Test/lint/E2E workflows present; not run locally. |

The actual runtime lock must record the selected immutable commit and export integrity separately from this research snapshot. Do not use a moving `main`, `edge`, or `latest` reference as the deployment identity. Local completion requires checking the pinned OSS export, dependency/build health, project-local Codex compatibility, account model discovery, isolated application state, loopback access, and the implemented fleet interfaces. Those setup results belong in the implementation verification record; they remain pending within the scope of this research document.
