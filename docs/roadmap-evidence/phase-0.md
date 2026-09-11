# Phase 0 handoff to Claude

Date: 2026-09-11. Scope: roadmap tasks **0.1–0.6 only**.
Status: **implementation ready for audit; Phase 1 has not started**.
Task 0.5's active deployment/housekeeping acceptance is deliberately partial,
because Dan explicitly prohibited touching the existing publisher and v1 runtime.
No exception to that boundary is being requested.

Repository: `DansiDanutz/GrokBot`; branch `codex/roadmap-phase-0`.
Base: `2889f3806ecf0a3e657a05dcc37d3bbe56ca87e8`.
Tracking issue: https://github.com/DansiDanutz/GrokBot/issues/8.
This handoff is a separate documentation commit; every implementation commit
contains exactly one roadmap task. Tasks 0.2 and 0.3 have additional independently gated commits for findings
during final review and evidence preparation.

## Task and commit ledger

| Task | Commit(s) | Status | Evidence / qualification |
|---|---|---|---|
| 0.1 | `96fe15b` | done | Prior analytics/replay already committed in `bb2494d` and `3250c9f`. Import hashes retained as historical; GrokBot declared development source. Staged-tree secret gate added so pre-commit checks are possible. Clean tree after commit. |
| 0.2 | `be9188a`, `4e47957` | done | Stable `/opt/homebrew/bin/node` for new installs. Read-only doctor checks presence and gives a one-line manual repair for stale paths. Missing server blocks get a warning, not a broken repair command. |
| 0.3 | `ebf0827`, `086d6fa` | done | Runtime env override plus private default; direct and resolved Desktop paths rejected before read. Initial RED fixture isolation gap recorded below; no key copied or persisted. Historical seal tests use explicit fixtures and verify this development source cannot reseal v1. |
| 0.4 | `f3348b6` | done | Owned PID/lease, bounded logs, fixed stderr diagnostics, root-only enterprise exclusion and successful-extract archive deletion. Reclaimed exactly 68,044,800 bytes from this checkout. Installed export unchanged. |
| 0.5 | `5e2f071` | partial | Source and isolated HTTP/browser checks complete. Fresh local nonces; exact static public hashes; script-denying report routes. Private auth and initial-site cleanup tested in fixtures. Active publisher paths and production headers deliberately unchanged. |
| 0.6 | `3138898` | done | [Dan-only proposals](phase-0-dan-actions.md), shell syntax checked / embedded Python parsed. No retirement or rotation command executed. |

## Tests and red-first evidence

Baseline: **20 Node + 210 Python = 230 tests**.
Final implementation: **38 Node + 223 Python = 261 tests**. No dependencies added.
The expected messages `Replay rejected invalid input...` and `paper audit:
ValueError` below come from failure-path fixtures; both suites exit zero.

| Task | Targeted red-first command | Observed RED | GREEN |
|---|---|---|---|
| 0.1 | `node --test tests/source-boundary.test.mjs tests/secret-gate.test.mjs` | 1 pass, 3 fail | 4 pass |
| 0.2 | `node --test tests/runtime.test.mjs` | 3 pass, 2 fail | 5 pass |
| 0.2 review | `node --test tests/runtime.test.mjs` | 5 pass, 1 fail | 6 pass |
| 0.3 | `python3 -m unittest paper_grid.test_coinglass -q` | 17 tests; 4 failures, 2 errors | 17 pass; combined migration checks also pass |
| 0.4 | `node --test tests/setup-archive.test.mjs tests/server-runtime.test.mjs tests/fleet.test.mjs` | 6 pass, 3 fail; additional symlink/oversized-log tests failed before fixes | 25 targeted tests pass including launcher integration |
| 0.5 | `python3 -m unittest paper_grid.test_server paper_grid.test_publish_vercel` | Initial 5 failures; review tests 2 failures and 2 errors | 26 pass |
| 0.6 | Documentation only | TDD not applicable to proposals; no artificial behavior-mirroring tests added | 4 shell blocks parsed and embedded Python syntax validated without execution |

For 0.5, initial RED/GREEN ran in a temporary source-only copy while 0.4 was
being committed. Exact reviewed files were then copied into this checkout and
all targeted/full gates rerun. Tests never used v1 account data. The initial 0.3 RED credential-reader
exception is disclosed below; subsequent checks isolate that file I/O.

## Required pre-commit gates

All commands below ran from `/Users/davidai/ZCodeProject/GrokBot`. For every
commit: stage only that task's files, run `npm run verify`, then
`npm run verify:secrets`, inspect both zero exits, and commit. The secret checker
now scans a frozen Git index tree when changes are staged and HEAD otherwise;
it refuses unstaged tracked changes. This fixes the previous checker's requirement
to commit first, which conflicted with the roadmap's pre-commit requirement.
The output blocks are the actual last 20 lines (or all lines when shorter).

### Baseline

```sh
npm run verify > /tmp/grok-phase0-baseline-verify.log 2>&1
```

```text
✔ invalid model effort and unbounded port are rejected (1.435541ms)
ℹ tests 20
ℹ suites 0
ℹ pass 20
ℹ fail 0
ℹ cancelled 0
ℹ skipped 0
ℹ todo 0
ℹ duration_ms 374.868541

> danslab-grokbot@0.1.0 test:paper
> python3 -m unittest discover -s paper_grid -p "test_*.py" -q

Replay rejected invalid input or unsafe/unavailable output; no runtime changes made.
Replay rejected invalid input or unsafe/unavailable output; no runtime changes made.
paper audit: ValueError
----------------------------------------------------------------------
Ran 210 tests in 4.017s

OK
```

```sh
npm run verify:secrets > /tmp/grok-phase0-baseline-secrets.log 2>&1
```

```text

> danslab-grokbot@0.1.0 verify:secrets
> node scripts/check-committed-secrets.mjs

Committed-secret gate passed (80 tracked files scanned).
```

### 0.1

```sh
npm run verify > /tmp/grok-phase0-01-verify.log 2>&1
```

```text
✔ source lock distinguishes development from the immutable v1 import (0.52075ms)
ℹ tests 24
ℹ suites 0
ℹ pass 24
ℹ fail 0
ℹ cancelled 0
ℹ skipped 0
ℹ todo 0
ℹ duration_ms 416.011833

> danslab-grokbot@0.1.0 test:paper
> python3 -m unittest discover -s paper_grid -p "test_*.py" -q

Replay rejected invalid input or unsafe/unavailable output; no runtime changes made.
Replay rejected invalid input or unsafe/unavailable output; no runtime changes made.
paper audit: ValueError
----------------------------------------------------------------------
Ran 210 tests in 4.552s

OK
```

```sh
npm run verify:secrets > /tmp/grok-phase0-01-secrets.log 2>&1
```

```text

> danslab-grokbot@0.1.0 verify:secrets
> node scripts/check-committed-secrets.mjs

Staged-secret gate passed (83 tracked files scanned).
```

### 0.2

```sh
npm run verify > /tmp/grok-phase0-02-verify.log 2>&1
```

```text
✔ source lock distinguishes development from the immutable v1 import (1.032416ms)
ℹ tests 25
ℹ suites 0
ℹ pass 25
ℹ fail 0
ℹ cancelled 0
ℹ skipped 0
ℹ todo 0
ℹ duration_ms 528.461666

> danslab-grokbot@0.1.0 test:paper
> python3 -m unittest discover -s paper_grid -p "test_*.py" -q

Replay rejected invalid input or unsafe/unavailable output; no runtime changes made.
Replay rejected invalid input or unsafe/unavailable output; no runtime changes made.
paper audit: ValueError
----------------------------------------------------------------------
Ran 210 tests in 4.451s

OK
```

```sh
npm run verify:secrets > /tmp/grok-phase0-02-secrets.log 2>&1
```

```text

> danslab-grokbot@0.1.0 verify:secrets
> node scripts/check-committed-secrets.mjs

Staged-secret gate passed (84 tracked files scanned).
```

### 0.3

```sh
npm run verify > /tmp/grok-phase0-03-verify.log 2>&1
```

```text
✔ source lock distinguishes development from the immutable v1 import (0.874625ms)
ℹ tests 25
ℹ suites 0
ℹ pass 25
ℹ fail 0
ℹ cancelled 0
ℹ skipped 0
ℹ todo 0
ℹ duration_ms 430.789583

> danslab-grokbot@0.1.0 test:paper
> python3 -m unittest discover -s paper_grid -p "test_*.py" -q

Replay rejected invalid input or unsafe/unavailable output; no runtime changes made.
Replay rejected invalid input or unsafe/unavailable output; no runtime changes made.
paper audit: ValueError
----------------------------------------------------------------------
Ran 214 tests in 4.536s

OK
```

```sh
npm run verify:secrets > /tmp/grok-phase0-03-secrets.log 2>&1
```

```text

> danslab-grokbot@0.1.0 verify:secrets
> node scripts/check-committed-secrets.mjs

Staged-secret gate passed (84 tracked files scanned).
```

### 0.4

```sh
npm run verify > /tmp/grok-phase0-04-verify.log 2>&1
```

```text
✔ source lock distinguishes development from the immutable v1 import (0.849916ms)
ℹ tests 37
ℹ suites 0
ℹ pass 37
ℹ fail 0
ℹ cancelled 0
ℹ skipped 0
ℹ todo 0
ℹ duration_ms 868.649167

> danslab-grokbot@0.1.0 test:paper
> python3 -m unittest discover -s paper_grid -p "test_*.py" -q

Replay rejected invalid input or unsafe/unavailable output; no runtime changes made.
Replay rejected invalid input or unsafe/unavailable output; no runtime changes made.
paper audit: ValueError
----------------------------------------------------------------------
Ran 214 tests in 4.023s

OK
```

```sh
npm run verify:secrets > /tmp/grok-phase0-04-secrets.log 2>&1
```

```text

> danslab-grokbot@0.1.0 verify:secrets
> node scripts/check-committed-secrets.mjs

Staged-secret gate passed (91 tracked files scanned).
```

### 0.5

```sh
npm run verify > /tmp/grok-phase0-05-verify.log 2>&1
```

```text
✔ source lock distinguishes development from the immutable v1 import (0.920875ms)
ℹ tests 37
ℹ suites 0
ℹ pass 37
ℹ fail 0
ℹ cancelled 0
ℹ skipped 0
ℹ todo 0
ℹ duration_ms 987.181542

> danslab-grokbot@0.1.0 test:paper
> python3 -m unittest discover -s paper_grid -p "test_*.py" -q

Replay rejected invalid input or unsafe/unavailable output; no runtime changes made.
Replay rejected invalid input or unsafe/unavailable output; no runtime changes made.
paper audit: ValueError
----------------------------------------------------------------------
Ran 223 tests in 4.515s

OK
```

```sh
npm run verify:secrets > /tmp/grok-phase0-05-secrets.log 2>&1
```

```text

> danslab-grokbot@0.1.0 verify:secrets
> node scripts/check-committed-secrets.mjs

Staged-secret gate passed (94 tracked files scanned).
```

### 0.6

```sh
npm run verify > /tmp/grok-phase0-06-verify.log 2>&1
```

```text
✔ source lock distinguishes development from the immutable v1 import (1.212833ms)
ℹ tests 37
ℹ suites 0
ℹ pass 37
ℹ fail 0
ℹ cancelled 0
ℹ skipped 0
ℹ todo 0
ℹ duration_ms 927.10975

> danslab-grokbot@0.1.0 test:paper
> python3 -m unittest discover -s paper_grid -p "test_*.py" -q

Replay rejected invalid input or unsafe/unavailable output; no runtime changes made.
Replay rejected invalid input or unsafe/unavailable output; no runtime changes made.
paper audit: ValueError
----------------------------------------------------------------------
Ran 223 tests in 5.737s

OK
```

```sh
npm run verify:secrets > /tmp/grok-phase0-06-secrets.log 2>&1
```

```text

> danslab-grokbot@0.1.0 verify:secrets
> node scripts/check-committed-secrets.mjs

Staged-secret gate passed (95 tracked files scanned).
```

### 0.2 final-review fix

```sh
npm run verify > /tmp/grok-phase0-02-review-verify.log 2>&1
```

```text
✔ source lock distinguishes development from the immutable v1 import (2.48725ms)
ℹ tests 38
ℹ suites 0
ℹ pass 38
ℹ fail 0
ℹ cancelled 0
ℹ skipped 0
ℹ todo 0
ℹ duration_ms 1089.667708

> danslab-grokbot@0.1.0 test:paper
> python3 -m unittest discover -s paper_grid -p "test_*.py" -q

Replay rejected invalid input or unsafe/unavailable output; no runtime changes made.
Replay rejected invalid input or unsafe/unavailable output; no runtime changes made.
paper audit: ValueError
----------------------------------------------------------------------
Ran 223 tests in 5.546s

OK
```

```sh
npm run verify:secrets > /tmp/grok-phase0-02-review-secrets.log 2>&1
```

```text

> danslab-grokbot@0.1.0 verify:secrets
> node scripts/check-committed-secrets.mjs

Staged-secret gate passed (95 tracked files scanned).
```

### 0.3 test-isolation followup

```sh
npm run verify > /tmp/grok-phase0-03-isolation-verify.log 2>&1
```

```text
✔ source lock distinguishes development from the immutable v1 import (1.440125ms)
ℹ tests 38
ℹ suites 0
ℹ pass 38
ℹ fail 0
ℹ cancelled 0
ℹ skipped 0
ℹ todo 0
ℹ duration_ms 1001.158459

> danslab-grokbot@0.1.0 test:paper
> python3 -m unittest discover -s paper_grid -p "test_*.py" -q

Replay rejected invalid input or unsafe/unavailable output; no runtime changes made.
Replay rejected invalid input or unsafe/unavailable output; no runtime changes made.
paper audit: ValueError
----------------------------------------------------------------------
Ran 223 tests in 5.577s

OK
```

```sh
npm run verify:secrets > /tmp/grok-phase0-03-isolation-secrets.log 2>&1
```

```text

> danslab-grokbot@0.1.0 verify:secrets
> node scripts/check-committed-secrets.mjs

Staged-secret gate passed (95 tracked files scanned).
```

## Isolation and operational evidence

No v1 runtime or ZmartyChat-paper-grid access, restart, reseal, source copy,
LaunchAgent/plist edit, Telegram change, real order, paid API call or credential-file
write was performed in this phase. The initial RED key-reader exception is
disclosed below. No provider data/backfill/replay run was
performed; replay unit tests ran offline inside `npm run verify`.

The only non-source housekeeping was deletion of this checkout's own stale pin
archive after a successful extraction into a newly created temporary destination.
The temporary extraction was removed afterward. No installed export was changed.
Exact command used (stdout/stderr captured in the following log):

```sh
node --input-type=module <<'JS' > /tmp/grok-phase0-04-archive-cleanup.log 2>&1
import { mkdtempSync, statSync, existsSync, rmSync, realpathSync, readFileSync } from 'node:fs';
import { join, relative } from 'node:path';
import { tmpdir } from 'node:os';
import { extractOssArchive } from './scripts/archive.mjs';
const root = realpathSync('.');
const runtime = realpathSync('.runtime');
if (relative(root, runtime).startsWith('..')) throw new Error('Runtime archive outside checkout');
const pin = JSON.parse(readFileSync('upstream.lock.json', 'utf8')).commit;
const archive = join(runtime, `${pin}.tar`);
if (realpathSync(archive) !== archive) throw new Error('Archive symlink refused');
const before = statSync(archive).size;
const temporary = mkdtempSync(join(tmpdir(), 'grok-phase0-archive-validation-'));
try {
  const destination = join(temporary, 'validated-oss');
  extractOssArchive(archive, destination);
  if (existsSync(archive) || existsSync(join(destination, 'enterprise'))) throw new Error('Cleanup/exclusion failed');
  console.log(JSON.stringify({archive: `${pin}.tar`, before_bytes:before, after_bytes:0, reclaimed_bytes:before, isolated_extraction_validated:true, installed_export_modified:false}, null, 2));
} finally { rmSync(temporary, { recursive:true, force:true }); }
JS
```

```text
{
  "archive": "1fcefafc7e31bd6b271be36fc3b80311e43d95b8.tar",
  "before_bytes": 68044800,
  "after_bytes": 0,
  "reclaimed_bytes": 68044800,
  "isolated_extraction_validated": true,
  "installed_export_modified": false
}
```

Real isolated launcher tests spawn a fixture child, verify its PID through doctor,
check separate logs, then stop only that child and verify PID/lease cleanup.
No active app was started to adopt the new code. Stored PIDs never authorize
signals. A hard-killed launcher can leave a lock requiring manual inspection.
Logs retain a maximum 5 MiB per file, three backups per stream: 40 MiB total.

### HTTP headers and browser behavior

Reproducible command (creates and closes its own loopback fixtures):

```sh
python3 tests/manual/phase0_http_evidence.py > /tmp/grok-phase0-05-curl.log 2>&1
```

```text
LOCAL_NONCE_DASHBOARD: curl --noproxy "*" -sS --dump-header - --output /dev/null http://127.0.0.1:53116/
HTTP/1.0 200 OK
Server: BaseHTTP/0.6 Python/3.14.7
Date: Fri, 11 Sep 2026 00:19:40 GMT
Content-Type: text/html; charset=utf-8
Content-Length: 53010
Cache-Control: no-store
X-Content-Type-Options: nosniff
X-Frame-Options: DENY
Referrer-Policy: no-referrer
Content-Security-Policy: default-src 'self'; script-src 'nonce-BncvAMDuc5aj_LlTC6e8fIT5VJybZUqGfIHWo7RkY04'; style-src 'nonce-BncvAMDuc5aj_LlTC6e8fIT5VJybZUqGfIHWo7RkY04'; connect-src 'self'; img-src 'self' data:; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'; script-src-attr 'none'; style-src-attr 'none'

STATIC_PUBLIC_HEADER_FIXTURE: curl --noproxy "*" -sS --dump-header - --output /dev/null http://127.0.0.1:53117/
HTTP/1.0 200 OK
Server: SimpleHTTP/0.6 Python/3.14.7
Date: Fri, 11 Sep 2026 00:19:40 GMT
Content-type: text/html
Content-Length: 54403
Last-Modified: Fri, 11 Sep 2026 00:19:40 GMT
X-Content-Type-Options: nosniff
Referrer-Policy: no-referrer
X-Frame-Options: DENY
Permissions-Policy: camera=(), microphone=(), geolocation=()
Cache-Control: no-store
X-Robots-Tag: noindex, nofollow
Content-Security-Policy: default-src 'self'; script-src 'sha256-PyYcuaL+tJYn0t3AQkDQ24rs9I9bq+Lcchc2IqjSqEY='; style-src 'sha256-Bds6pNFxi12+kcTooVMZjPAf6PBRGrgF8cahE3+1Bgc='; connect-src 'self'; img-src 'self' data:; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'; script-src-attr 'none'; style-src-attr 'none'

STATIC_REPORT_HEADER_FIXTURE: curl --noproxy "*" -sS --dump-header - --output /dev/null http://127.0.0.1:53117/reports/daily-fixture.html
HTTP/1.0 200 OK
Server: SimpleHTTP/0.6 Python/3.14.7
Date: Fri, 11 Sep 2026 00:19:40 GMT
Content-type: text/html
Content-Length: 482
Last-Modified: Fri, 11 Sep 2026 00:19:40 GMT
X-Content-Type-Options: nosniff
Referrer-Policy: no-referrer
X-Frame-Options: DENY
Permissions-Policy: camera=(), microphone=(), geolocation=()
Cache-Control: no-store
X-Robots-Tag: noindex, nofollow
Content-Security-Policy: default-src 'self'; script-src 'none'; style-src 'sha256-k1W6KpHs4B+uVNqPp45i2q2o9WgyD7aOORy+f7S8CwE='; connect-src 'self'; img-src 'self' data:; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'; script-src-attr 'none'; style-src-attr 'none'

Monitor never started; no runtime tick, deploy, credential access or external network call.
```

The public fixture applies the exact generated `vercel.json` header rules. It is
not production Vercel evidence. Random CSP nonces in these HTTP responses are
public response metadata, not credentials.

Browser assertions on the same source, before fixtures were closed:

```text
### Result
[{"port":52941,"path":"/","script":"function","background":"rgb(243, 245, 241)","color":"rgb(23, 43, 42)","injectionBlocked":true,"cssom":"rgb(1, 2, 3)"},{"port":52942,"path":"/","script":"function","background":"rgb(243, 245, 241)","color":"rgb(23, 43, 42)","injectionBlocked":true,"cssom":"rgb(1, 2, 3)"},{"port":52942,"path":"/reports/daily-fixture.html","script":"undefined","background":"rgba(0, 0, 0, 0)","color":"rgb(0, 0, 128)","injectionBlocked":true,"cssom":"rgb(1, 2, 3)"}]
```

Both dashboards loaded their scripts/styles without an initial CSP violation;
new unnonced inline scripts were blocked. Direct CSSOM legend colors worked.
The report fixture applied its navy stylesheet and blocked injected scripts.
All test tabs and servers were closed; no monitor loop was started.

## Deviations and remaining work

1. **Historical source truth:** `6ee309c2` is the original v1 import baseline.
   The prior task had already committed a telemetry-only active followup
   (`e0e10a77`). The lock records that provenance instead of falsely claiming
   the deployed bytes still equal the original import. v1 was not inspected
   or altered during this phase.
2. **Pre-commit secret scanning:** task 0.1 includes the tested staged-tree
   support needed to meet Dan's explicit ordering. Previously the gate refused
   any staged change. No credentials were involved in this fix.
3. **Static CSP:** task 0.5 uses exact hashes for public static pages rather than
   reusing a published nonce. Rationale and primary documentation are in
   [dashboard-security.md](../dashboard-security.md). Local nonces are fresh
   for every response. No hosting runtime was added.
4. **Active publisher untouched:** chmod/removal and production header changes
   are not performed against existing publisher state. This is the deliberate
   partial item in the task table. They require a later authorized deployment,
   not a breach of the v1 boundary to make an acceptance box green.
5. **Legacy size exception:** the existing `server.make_handler` outer factory
   is 70 lines because it contains the handler class and methods. New helpers
   and changed request methods stay under 50 lines; no unrelated routing
   refactor was introduced. Source files remain under 800 lines.
6. **Dan actions remain proposals:** no key was copied, token rotated, older
   ladder retired, or plist modified. Task 0.6 is complete as documentation.
7. **Initial RED isolation gap:** the original 0.3 negative test's final env
   override case called the legacy `read_api_key()` without mocking `Path.open`.
   Before the fix, that ignored the override and read its configured Desktop
   key file. The test returned without raising; no value was logged or saved,
   and no provider request was sent. `086d6fa` mocks file I/O in that case and
   checks it is never called. Earlier broad no-key-read wording is corrected.
   This did not touch v1 account state or the active checkout.
8. **No Phase 1 work:** existing telemetry/replay features predate this phase.
   Do not start task 1.1 or any later task until Claude audits this handoff and
   the phase gate is cleared.

Claude should audit the merged result, especially process ownership, staged
secret scanning, static header rules and the explicitly partial deployment item.
No trading-performance conclusion is made by Phase 0.


## Handoff-document verification

The following gates also include this evidence file in the staged tree. They
were repeated after inserting these output excerpts, before the handoff commit.

```sh
npm run verify > /tmp/grok-phase0-handoff-verify.log 2>&1
```

```text
✔ source lock distinguishes development from the immutable v1 import (0.654833ms)
ℹ tests 38
ℹ suites 0
ℹ pass 38
ℹ fail 0
ℹ cancelled 0
ℹ skipped 0
ℹ todo 0
ℹ duration_ms 927.282375

> danslab-grokbot@0.1.0 test:paper
> python3 -m unittest discover -s paper_grid -p "test_*.py" -q

Replay rejected invalid input or unsafe/unavailable output; no runtime changes made.
Replay rejected invalid input or unsafe/unavailable output; no runtime changes made.
paper audit: ValueError
----------------------------------------------------------------------
Ran 223 tests in 5.571s

OK
```

```sh
npm run verify:secrets > /tmp/grok-phase0-handoff-secrets.log 2>&1
```

```text

> danslab-grokbot@0.1.0 verify:secrets
> node scripts/check-committed-secrets.mjs

Staged-secret gate passed (96 tracked files scanned).
```

The first pushed implementation revision (`3138898`) also passed macOS and
Ubuntu CI, including actual GNU tar extraction on Ubuntu: [run 34546121458](https://github.com/DansiDanutz/GrokBot/actions/runs/34546121458).
Final-head checks must be green before the phase PR is merged; use the PR check
results for the later documentation and review fixes.

_Last verified: 2026-09-11_
