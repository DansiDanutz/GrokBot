# Dashboard response security

These changes apply to this development source. Phase 0 does not deploy them to
v1, read its runtime, invoke its publisher, or alter any existing LaunchAgent.

The local dashboard creates a fresh 256-bit nonce for each HTML response. Only
the reviewed dashboard template receives script nonces. Saved local audits get
style nonces and `script-src 'none'`. API and error responses deny scripts and
styles. Content lengths are calculated after nonce insertion.

The public site remains a static export. A nonce embedded at publication time
would be reused for every visitor until the next publication. Instead, the
publisher calculates SHA-256 hashes over the exact inline script and stylesheet
bytes. Separate, non-overlapping CSP routes cover `/`, `/index.html`, `/reports/`
and `/data/`; common security headers do not set another CSP. Reports authorize
only their stylesheet hashes and deny scripts. This is an intentional deviation
from roadmap task 0.5's request for nonces on both dashboards: the static host has
no per-response renderer. No new hosting runtime or dependency was introduced.

Both policies deny inline event handlers, style attributes, object embedding,
framing and form submission. The chart legend's direct `element.style.background`
assignment continues to work; no `unsafe-inline` exception was added.
[MDN explains nonce/hash selection](https://developer.mozilla.org/en-US/docs/Web/HTTP/Guides/CSP)
and [direct CSSOM assignments](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Content-Security-Policy/style-src).
[Vercel documents static response headers](https://vercel.com/docs/project-configuration/vercel-json).

When explicitly invoked against its caller-selected publisher state, publication
validates `auth/` and makes it mode 0700. It rejects a symlink or non-directory
`initial-site/` before deployment and deletes that bootstrap directory only
after a successful replacement deployment. Cleanup failure preserves the new
published URL and success timestamp, recording a fixed `cleanup_warning` without
exception messages. Phase 0 exercises these mutations only in temporary fixtures;
it does not remove or chmod any active publisher path.

Reproduce HTTP evidence without providers, a worker thread or credentials:

```sh
python3 tests/manual/phase0_http_evidence.py
```

The script creates temporary source-only fixtures, uses ephemeral loopback ports,
applies the generated Vercel header rules in a fixture server, runs curl against
both dashboards and a styled report fixture, and closes both servers. Its public
headers demonstrate generated configuration, not a production Vercel deployment.
Pass `--browser` to keep these fixtures open until Enter for manual browser checks.

The existing `make_handler` outer factory remains over 50 lines because it wraps
the handler class and its methods. New helper functions and changed request
methods are below the roadmap's 50-line limit. Splitting the entire handler
factory is deferred to avoid unrelated routing changes.

_Last verified: 2026-09-11_
