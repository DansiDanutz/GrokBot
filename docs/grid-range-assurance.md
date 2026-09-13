# Paper grid range and cost assurance

Issue: https://github.com/DansiDanutz/GrokBot/issues/52
Reviewed production base: `7244d26174fac1ceb4dcab74c41ab105b6e257df`.
This source change targets the current `design/polish` paper runtime. The native
Secretary and team configuration are maintained separately in PR50.

## Entry evidence

New entries require a versioned, consistent range proof. The radar uses the
latest complete 168 hourly candles, rejects gaps, and confirms a pivot using two
completed candles on either side. A level needs at least two tests separated by
three hours. Four-hour and daily direction calculations use complete buckets.
Historical analyses cannot read later ticker/book observations.

The selector considers confirmed ranges in increasing width, rounds boundaries
inward to the exchange tick and selects the highest feasible count in that range.
The count must pass the pair return, entry split and lot/liquidation constraints.
The record distinguishes the fee-only ceiling from the selected feasible count
and records rejections of higher counts within that range.

New KuCoin futures bot fills use **0.06% each**, including maker fills, consistent
with [KuCoin's bot FAQ](https://www.kucoin.com/support/21960469554201).
Every complete adjacent pair must exceed 1% return on its allocated margin at
5x after both fees; existing 12–200 grid and portfolio limits remain. Actual
emitted lines, including tick truncation, determine the worst pair. Existing
saved fee rates and the old missing-field fallback retain their historical
meaning; this change does not rewrite booked PNL or certify legacy entries.
Existing layouts that fail the official-fee floor on their actual grid lines
enter the existing PROFILE_UPDATE close path. Its non-risk minimum hold remains;
boundary risk exits never wait for that hold. Compliant legacy layouts remain.

Each new entry freezes original and rounded bounds, pivot times/confirmations,
history coverage/hash, grid layout, quantities, actual fees, seeded-close costs,
spread and four-hour funding sensitivities. Conditional completed-pair counts
show recovery of modeled setup/exit costs under stated assumptions. They exclude
unknown future inventory losses and do not guarantee whole-bot break-even.
Unknown funding or spread remains unknown. CoinGlass price-level heatmaps are
explicitly unavailable/not wired; executed liquidation totals are not clusters.

Full dossiers are private, write-once files under the state directory's
`setup-evidence/<sha256>.json`. They outlive the rolling 30-day bot history.
Pending copies are checkpointed and retried; publication verifies existing
content instead of overwriting it. Public snapshots contain a bounded allowlisted
summary and the full dossier's ID. Legacy entries say MISSING; today's data never
reconstructs supposedly contemporaneous proof for them.

## Boundary and accounting behavior

A newer observed quote touching or crossing either boundary closes the paper
position, regardless of loss or non-risk minimum hold. Funding lookup errors or
incomplete startup recovery cannot veto this close. Partial successful recovery
transitions and CLOSE journals survive a later symbol's error and restart.

Unresolved funding/exposure or recovery coverage is retained as an explicit
reconciliation obligation. Affected PNL remains provisional; new admissions and
automatic learned-rule changes pause until actual evidence resolves obligations.
There is no fabricated reconciliation or automatic obligation clearing. Archive
failures preserve safety closes and state checkpoints while blocking admissions
when evidence cannot be retained safely.

This is a sampled paper engine, not an exchange-side stop: ten-second polling,
feed outages and synthetic OHLC recovery cannot establish the exact unobserved
crossing time or execution price. A profitable grid pair does not ensure a
profitable bot. Net remains realized + unrealized − fees − signed funding.
Inactive-bot rotation remains a separate research/implementation need; no
untested churn rule is introduced here.

## Verification and release

Run `npm run verify` and `npm run verify:secrets` on the staged tree. Focused
regressions cover observed loss exits under funding/recovery failure, committed
recovery journals, accounting gates, evidence retention/retry/tamper handling,
range continuity/causality, actual-line maximality, legacy fees, real-epoch entry
admission, public field filtering and cost sensitivity arithmetic.

Deploy only the reviewed revision with a single paper writer and preserved
ledger, source/config rollback and heartbeat/readback checks. An open PR or a
passing offline test is not evidence of a production deployment. The installed
research bridge's separate source pin must be reconciled explicitly if its
production source checkout changes. No real exchange orders or new credentials
are part of this change.
