# Paper ticker outage recovery — 20 September 2026

Issue: #66. An anonymous allTickers response contained ETHBTCUSDTM with price
zero. The strict numeric parser rejected the whole response, suppressing valid
quotes for open paper bots. A read-only probe with the corrected parser returned
682 valid quotes including all six required major/open symbols.

Invalid numeric prices are now isolated per contract; malformed identities,
duplicate symbols, invalid envelopes and an entirely invalid price set still
fail. Missing majors or open-bot symbols keep health unhealthy and block new
decisions. Available quotes still drive immediate boundary exits. On feed
recovery the existing one-minute recovery path must complete before new fills
and entry decisions resume. Candle recovery cannot reconstruct unseen tick
ordering or prove the exact time a boundary was first crossed.

Five new offline regression tests cover isolation, duplicate identity, missing
open quotes, boundary protection during partial responses and recovery before
entry decisions. Red tests reproduced both the original parser failure and
incorrect healthy/decision behavior. `npm run verify` passed 1,195 tests
(39 Node, 356 paper, 776 trader, 24 team). The staged secret gate is also required.

Deployment preserves the existing persisted start, bankroll, closed positions,
equity history and pinned bot policies. No exchange order or strategy activation
is part of this change. Funding allocation and historical profitability are
separate evidence questions; this repair makes no profitability claim.
