# Audit follow-up: instrumentation and credential handling

The existing Telegram token was retained. Seven legacy LaunchAgent definitions
now load it through a private credential file and an executable wrapper rather
than carrying the value in their environment blocks. Four previously loaded idle
jobs were reloaded; disabled/unloaded jobs remained so. No bot messages or token
rotation were performed. The wrapper reads a file owned by the user with mode
600, rejects symlinks, and never prints values. The enclosing credential directory
is mode 700; protected rollback backups are outside the repository. No credential
file or machine-specific LaunchAgent is committed here. This limits accidental
configuration disclosure; it does not isolate secrets from other processes with
the same user privileges.

Paper changes are instrumentation only: reason-coded rejected buys, per-tick
equity, matching audit filter logic and a deterministic offline replay harness.
A strict migration admits only the known baseline and reviewed target source
hashes, checks the original configuration, records a telemetry boundary and
preserves existing accounts/history. A frozen or unknown experiment is rejected.

The first replay used the preserved pre-experiment account, verified against the
paused original ledger, and the actual saved observations. Two executions were
identical and their final states matched both live accounts. No replacement
strategy or profitability claim follows from a zero-trade sample.

Rotation, reward/risk, discovery ranking and funding model remain the original
baseline. They should be compared in explicitly versioned offline experiments,
not silently changed during this telemetry upgrade. Node path durability, the
CoinGlass Desktop path and the older ladder job require separate follow-up.

Verification includes state/fill parity across twelve pre-change scenarios,
rejection and sampling tests, private-loader tests, deterministic replay tests,
and live service/publication checks.
