# GrokBot for Dan's Lab

A Mac Studio integration of the **OpenMausBot open-source runtime**, configured for GPT-6 Astra through the official Codex CLI and an existing ChatGPT sign-in. The app retains the upstream OpenMausBot interface and attribution; GrokBot is the name of this integration project.

The [repository comparison](docs/repository-research.md) explains the selection. [upstream.lock.json](upstream.lock.json) pins the reviewed source and its successful CI run. The runtime is exported without `enterprise/`, with upstream licensing and notices intact. Custom branding and other enterprise features are not enabled.

## Continuous paper research and public dashboard

**[DansLabTrader.vercel.app](https://danslabtrader.vercel.app)** publishes the
continuous paper comparison every 30 minutes. The complete source is now in
[`paper_grid/`](paper_grid/README.md): public KuCoin discovery, CoinGlass
liquidation filtering, two virtual accounts, cost-aware execution, risk limits,
48-hour audits, daily/weekly summaries, and a sanitized static Vercel publisher.
It uses Python 3.10+ standard-library modules and makes no live exchange orders.
The [offline replay harness](paper_grid/REPLAY.md) reproduces saved decisions from an explicit historical checkpoint.
The [learning lab](paper_grid/ANALYTICS.md) adds period/account filters, activity
charts, win/loss and exit-reason analysis, additional-buy cohorts and a trade journal.

The installed **Grok Bot** app reviews this separate service. It is distinct from
the OpenMausBot foundation below. See [operating paths and reporting](docs/paper-operations.md),
[reusable Grok instructions](config/paper-review-instructions.md), and
[public publisher setup](paper_grid/PUBLIC_WEB.md). The running experiment remains
in its original checkout; importing source here does not restart or relocate it.

`config/paper-source.lock.json` records the original revision and file hashes.
Only source, tests and documentation are included. Private ledgers, credentials,
chat history and generated snapshots remain local.

## Native paper review team

The [five-bot review team](docs/paper-team.md) collaborates in the native Grok Bot
**Paper Grid Trading Team** group, with one hourly Lead-owned routine.
[Reproducible instructions](config/paper-team/contract.md) preserve the deployed
grid strategy and keep the existing deterministic paper engine as sole executor.
The [research and source limits](docs/paper-team-research.md) cover primary code,
exchange documentation, X and YouTube.

## Run the OpenMausBot foundation locally

Requires macOS ARM64, Node 24+, Git, npm, and a Codex ChatGPT sign-in. This repository installs its own Codex and pnpm versions; it does not upgrade global tools.

```sh
npm ci --ignore-scripts
npm run setup
npm run doctor
npm start
```

Open **http://127.0.0.1:8871**. Stop the foreground launcher with Ctrl-C. Ports 8871/8872 avoid the existing service on upstream's default 8799. Setup does not submit model requests, install a background service, enable a public tunnel, or connect a Telegram bot. Your chats use your subscription quota when you send them.

Use the model picker to confirm **GPT-6 Astra** is available for your account. If the local Codex CLI needs sign-in, use `./node_modules/.bin/codex login`; credentials stay in Codex's normal credential store. Existing API keys are not imported. An unavailable selected model is a setup problem, not permission to substitute another model.

The first configuration uses Astra with low reasoning effort, two concurrent threads per bot, and at most one shared local VM if you later enable VMs. The concurrency setting is per bot, not a global resource cap. Computer-control and remote execution capabilities require their own explicit configuration in the upstream app.

## What this adds

- Reproducible source pin and an OSS-only runtime export.
- Project-local Codex `0.154.0` with GPT-6 Astra as the initial model.
- Private runtime/configuration under `.runtime/`, independent of existing OpenMausBot and fleet state.
- Loopback-only startup with occupied-port detection and owned-process readiness checking.
- `danslab_status` MCP tools for aggregate machine resources and fixed local service reachability.
- A Mac Studio [bot instruction template](config/bot-instructions.md).
- Offline unit/contract tests and a [verification record](docs/verification.md).

`npm run fleet` reports local Paperclip, OpenClaw, routing-oracle, Codex-proxy, and Ollama TCP reachability. It does **not** establish health, authenticate to these services, dispatch work, change routing, or control droplet agents. This deliberately small integration can coexist with the existing fleet. No live fleet configuration is rewritten.

## Configuration and updates

Edit `config/workspace.json` before the first setup. Once initialized, the upstream app owns `.runtime/data/config.json`; setup preserves it byte-for-byte. Change subsequent model, reasoning and account choices in the app. The launcher's port remains controlled by `config/workspace.json`; the adjacent port is reserved for upstream webhooks.

The upstream source and build live at `.runtime/<commit>/`. Treat them as immutable. To update: research a new revision, change the pin, run setup and verification, then restart. Each revision has its own export. Back up `.runtime/data/` while stopped before an update; rolling back code alone cannot undo a data migration. Never run `git clean -fdx` on this checkout.

The repository is separate from the runtime. Keep `.runtime/`, API keys, transcripts and machine snapshots out of Git. Sharing this integration does not share the local app state.

## Development

Node 24+ and Python 3.10+ are required for the complete offline verification gate.

```sh
npm run verify
```

For upstream behavior, follow the pinned runtime's `docs/verification/README.md`. Use its isolated fake-engine fixture, never a model turn against the user's active account. Upstream desktop packaging, paid providers, cloud VMs and fleet task dispatch are separate acceptance work.
