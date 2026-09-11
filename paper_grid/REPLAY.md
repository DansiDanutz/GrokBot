# Offline replay from recorded evidence

`replay.py` replays the pure paper engine against saved market observations. It
makes no provider requests, imports no credentials, executes no exchange orders,
and never updates the running experiment. Use it to reproduce a decision or test
an explicitly chosen configuration change. It does not search for profitable
parameters or activate a strategy.

## Required historical checkpoint

The input is a JSON object with these fields:

| Field | Meaning |
| --- | --- |
| `schema` | Integer `1` |
| `mode` | `paper` |
| `start_at` | Unix timestamp of the supplied historical checkpoint |
| `config` | Complete configuration, including every `engine.default_config()` key |
| `initial_state` | Explicit engine state at `start_at`, before the observations being replayed |
| `observations` | List of `{time, market, coinglass?, skipped?}` records |

A new-account checkpoint has `last_run: null`; its first observation can equal
`start_at`. A checkpoint taken after a tick has `last_run` set, and every replayed
observation must be strictly newer than that value. Position funding and opening
times, cooldown timestamps, and the last run cannot be newer than `start_at`.

Verify the checkpoint against a dated backup or other preserved evidence before
using a real run. **Never copy today's account and label it as yesterday's starting
state.** The harness deliberately has no option to infer an initial account from
current runtime balances. Both comparison arms start with independent copies of
the supplied checkpoint; a mid-run checkpoint comparison therefore answers what
both strategies would do from that same specified state, not their independent
lifetime performance.

Market records use the exact engine quote schema. `coinglass` holds the saved
feature object consumed by `coinglass.apply_filter`. Missing or stale features
block new filtered-arm risk while preserving exit quotes. Future market quotes
fail the engine's normal freshness gates and are identified in the per-tick
history. Records marked `skipped` represent aborted cycles and do not execute.

## Commands

Create a private directory outside the checkout and runtime. Supply real absolute
paths without symlink components. On macOS `/tmp` and `/var` are symlinks; use the
resolved `/private/...` path when appropriate.

Replay an already prepared fixture:

```sh
python3 paper_grid/replay.py \
  --input /absolute/private-research/replay-input.json \
  --output /absolute/private-research/replay-result.json
```

Without `--output`, only a compact aggregate summary is printed. It omits account
states, raw quotes, event contents and local source paths.

To collect saved observations, prepare a checkpoint containing all required
fields **except `observations`**, then export:

```sh
python3 paper_grid/replay.py \
  --runtime /absolute/paper-runtime \
  --checkpoint /absolute/private-research/checkpoint.json \
  --export-input /absolute/private-research/replay-input.json
```

Export reads `experiment.json` and the archive files declared in its manifest,
selects records following the explicit checkpoint, and validates the result. It
never reads `accounts` to establish starting state. Missing declared archives,
conflicting timestamps and malformed records fail closed. Export is read-only but
is not a transactional snapshot of a concurrently changing runtime; for exact
forensic reproduction use a previously captured, consistent runtime copy.

An optional JSON object provides explicit configuration overrides:

```sh
python3 paper_grid/replay.py \
  --input /absolute/private-research/replay-input.json \
  --config-overrides /absolute/private-research/variant.json \
  --output /absolute/private-research/variant-result.json
```

Overrides are recorded in provenance, validated by the paper engine, and applied
only to the replay. They never tune or alter the running strategy. Unknown keys
are rejected. Compare identical observation sets and checkpoints, and reserve
later, unseen observations for evaluating a hypothesis selected on earlier data.

## Evidence and limits

The full result contains a deterministic replay ID; input, checkpoint and config
hashes; hashes of the engine, filter and replay implementation; per-tick equity;
all generated events and buy rejections; entry/add/close counts; close reasons;
fees; modeled funding; sampled drawdown; and final paper states for both arms.
No current wall-clock timestamp enters the result. The code hashes describe the
implementation used for replay; they do not prove it matches the historic engine.

`net_equity_change` compares the checkpoint's marked equity with the final mark.
`realized_pnl_change` is the change in cumulative realized P&L. A position opened
before the checkpoint can include earlier entry fees in its closing trade's net
P&L. `execution_fees` counts only fees generated during replay; funding is the
change in accrued plus closed modeled funding. These quantities have different
period boundaries and should not be added together as independent costs.

- Observations are sorted chronologically; exact duplicate records are removed.
  Differing records at one timestamp are rejected rather than silently chosen.
- Inputs, combined export sources and output are limited to 32 MiB, with at most
  25,000 input observations and 100,000 generated events across both accounts.
  Oversized runs must be split using explicitly preserved checkpoints. Output
  never silently drops equity marks or events.
- Output requires a new file. Existing files, repository paths, symlinks, and
  overlap with protected input/runtime paths are rejected. Exported observations
  are private research data, not a sanitized public dashboard payload.
- Recorded quotes cover the historic shortlist plus symbols held at the time.
  They do **not** describe the full universe. A variant can select a symbol whose
  later quotes were never recorded; `coverage_incomplete` and per-tick unpriced
  positions identify this limitation. Such results cannot validate an unbiased
  universe-wide strategy sweep.
- Equity uses the engine's last-known bid if a held symbol is missing. Estimated
  equity and insufficient exit depth are reported explicitly. The checkpoint
  mark for already-held positions is also an estimate.
- Drawdown is measured at saved ticks, not every market move. The simulator cannot
  infer intratick stops, order queue fills or exchange liquidation. It does not
  substitute a candle's minimum price as a hindsight execution price.
- Successful replay demonstrates reproduction of the stated model and inputs,
  not future profitability or real-exchange execution performance.

Verify offline:

```sh
python3 -m unittest paper_grid.test_replay -v
```
