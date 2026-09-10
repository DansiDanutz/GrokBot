# DansLabTrader public paper dashboard

The public page is https://danslabtrader.vercel.app. It displays sanitized snapshots
of the two independent paper accounts. It cannot place orders, control the local
engine, or access exchange credentials. Simulated results do not establish future
profitability.

A separate Mac LaunchAgent invokes `publish_vercel.py` every 1,800 seconds. Each
invocation reads the existing published experiment snapshot and archived reports,
checks local worker health if available, exports a bounded public DTO into a new
temporary directory, and deploys that directory to the explicitly linked Vercel
production project. No dependency installation, Git checkout, Vercel pull, or
provider API access is part of publication. The temporary directory is deleted
after each attempt. A separate nonblocking file lock prevents concurrent runs.

The public page polls its same-origin static files every minute. Fresh data still
depends on the 30-minute Mac publisher. Its timestamp and stale indicator show
when the last public snapshot was produced. If the Mac sleeps, loses connectivity,
or authentication expires, the last successful Vercel deployment remains available;
the page does not keep receiving new results. Vercel failures leave the local
paper service running and do not reset positions, cash, reports, or audit schedules.
Daily, weekly and 48-hour reports appear after local generation and the next
successful publication. Up to 100 recent reports and 336 history points are public;
the full archive stays local.

## Local configuration

Keep publisher state outside the trading runtime:

```json
{
  "project_id": "prj_x7AZnstTOQGAbD7z8LToVMG978v9",
  "org_id": "team_Qtajbnyu0ZBt3TGPBIbgiyg1",
  "scope": "irises-projects-ce549f63",
  "node": "/opt/homebrew/bin/node",
  "cli": "/Users/davidai/.npm/_npx/69f9afb961c37556/node_modules/vercel/dist/vc.js",
  "public_url": "https://danslabtrader.vercel.app"
}
```

Save this nonsecret project configuration at
`/Users/davidai/Sandbox/grokbot/vercel-publisher/config.json`. CLI authentication
belongs only in the isolated `auth/` subdirectory of that state directory; never
copy it into the source tree or public site. The cached CLI and Node must remain
available at their configured locations. Reauthenticate using the Vercel CLI's
`login --global-config /Users/davidai/Sandbox/grokbot/vercel-publisher/auth` when
needed. Do not put a token on the command line.

Run manually from the checkout:

```sh
python3 -m paper_grid.publish_vercel \
  --runtime /Users/davidai/Sandbox/grokbot/zmarty-paper-runtime \
  --state /Users/davidai/Sandbox/grokbot/vercel-publisher
```

The same command works through the absolute `paper_grid/publish_vercel.py` path
for a LaunchAgent. It exits nonzero on failure; busy overlap exits successfully
without deploying. `publisher.json` records attempt time, last successful snapshot
time, deployment URL and sanitized failure category. CLI output and credentials
are not logged. A deployment has a 240-second timeout. The production hostname is
stable; deployment URLs are immutable individual versions. A timeout can be
ambiguous if Vercel already accepted the deployment, so check the hostname before
assuming nothing changed.

Public assets contain an allowlisted paper DTO and generated sanitized report
files, never raw runtime documents, private filesystem paths or raw CoinGlass
liquidation series. Response headers disable caching and framing, restrict scripts
and network requests to the same origin, and request that crawlers avoid indexing.
The site is still publicly accessible to anyone who has its URL; `noindex` is not
access control. Vercel production deployment limits apply to each scheduled update.

Publisher tests mock Vercel execution and cover project binding, credential
isolation, deployment failure/timeout, preserved last-success state, symlink and
overlap rejection, and concurrent-run locking. Exporter tests separately verify
the publication data boundary. A real deployment and public endpoint checks are
required when changing Vercel configuration.

## Activation evidence — 11 September 2026

Production: https://danslabtrader.vercel.app/

Installed LaunchAgent: `com.danslab.trader-publisher`, loaded in `gui/501`,
`RunAtLoad=true`, `StartInterval=1800`. Its first real background deployment
completed successfully and the public report timestamp matched `publisher.json`.
All four public routes returned HTTP 200 with no-store headers. Private runtime,
auth and environment paths returned HTTP 404. The original continuous paper
worker remained running with no cycle or audit error.

Grok Bot's existing continuous 30-minute routine now includes the public reports
link and reads sanitized publisher status. It reports new publication failures or
a last successful publication older than 45 minutes, without creating another
routine or changing paper strategy rules.

Verification: 159 isolated paper-system tests passed, JavaScript syntax passed,
and narrow and desktop browser views loaded real exported data. No reports were
due at activation; future archived report generation/export is covered by tests.
Automatic operation over subsequent days and authentication lifetime have not
yet been observed. Logs are under the separate publisher state directory.

## Learning analytics

`/data/analytics.json` contains separately timestamped, sanitized historical
analytics for the learning lab. Its generation can fail independently without
blocking portfolio publication. See [ANALYTICS.md](ANALYTICS.md) for definitions,
period boundaries and the limits of interpreting paper results. The current
source extends the initial four-file export with this additional JSON document.
