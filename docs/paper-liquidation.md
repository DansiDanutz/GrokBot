# Paper accounting version 2 and liquidation protection

All directions use 1,000 USDT margin at 5×, plus a separately allocated 200 USDT
reserve. Five bots allocate 6,000 USDT from the 10,000 USDT account. Leverage
enters quantity sizing once; USDT profit is never multiplied by leverage again.
This is a paper model, not an exchange-confirmed KuCoin allocation or liquidation
quote. Nothing places real exchange orders.

## Quantity and grid accounting

Support and resistance fix the range. The largest arithmetic grid count, capped
at 200, must have an estimated allocated-margin return strictly above 1% after
both 0.06% execution fees at every full adjacent grid pair. Funding, entry costs,
partial first pairs and exit losses are separate; the floor does not guarantee
positive total bot profit.

Sizing uses contract multiplier and lot size, rounds down to whole permitted
contract lots, and budgets the maximum planned adverse-fill cost at 5×. A risk
check may reduce that size further. RAY has two exact observation lookups only:
1,000 margin, 5×, 70 grids, upper 2, unit multiplier/lot increment 1; Long lower
1.4 and entry within 0.0001 of 1.5468 gives 39 lots, Neutral lower 1.1 and entry
within 0.0001 of 1.574 gives 17 lots. The liquidation guard still applies. Other
entries use conservative modeled sizing. These observations are not a universal
KuCoin quantity formula, and the preview's 37 Long lots are not the executed 39.

Neutral keeps separate Long and Short books, their own inventory, average entry,
orders and unrealized PnL. Each seeded quantity equals its seeded closing-order
count times quantity per order. Only a matched closing fill completes a grid;
profit uses its actual matched entry, including the opening price for seeded
inventory. A first seeded close can span only part of an interval. Closing the
bot flattens both books and charges execution fees without manufacturing grids.
Grid profit is a diagnostic gross paired-profit ledger, not another balance
credit: true net is realized + unrealized − fees − funding.

## Funding evidence

The autopilot uses stored contract funding schedules and settlement history,
with no eight-hour fallback. Recorded settlements take precedence. A fresh
pre-settlement snapshot can estimate only its explicitly announced next funding
boundary; its rate is not extrapolated across missing historical boundaries.
The persisted cursor charges each observed boundary once on the pre-update
signed position at its last known price. Neutral's signed aggregate represents
the sum of the two legs' funding at the same rate and mark.

Status is RECORDED for recorded evidence without a current estimated schedule,
ESTIMATED when a fresh announced schedule or estimated settlement is used, and
UNAVAILABLE when evidence is absent. Missing historical charges are not invented
or silently treated as a known zero; historical ledgers are retained. Settlement
valuation still uses the last observed paper price, not an exchange settlement
statement.

## Admission and ongoing protection

Before opening, model adverse grid fills to each range edge. Require the modeled
Long liquidation price below lower × 0.99 and Short liquidation price above
upper × 1.01, plus coverage by the recorded lowest risk-tier limit. Initial
admission excludes reserve. Neutral uses a conservative collateral split across
its independent legs. Missing or invalid risk metadata rejects admission; a
whole-lot quantity that cannot satisfy the buffer is rejected.

For signed base quantity q, entry E, collateral C and r = maintenance rate +
assumed 0.06% liquidation fee, the isolated estimate is:

    liquidation price = (q * E - C) / (q - abs(q) * r)

Collateral includes margin, any reserve already transferred, realized PnL,
fees and funding. Quantity is already in base units: never apply the multiplier
again. Maintenance rate and lowest-tier cap come from stored public contract
metadata. Freshness limit is 120 minutes. Flat inventory has no liquidation
price; invalid, stale, nonpositive and uncovered-tier outcomes are explicit.

During updates, if the current buffer is threatened, the committed 200 reserve
is transferred into collateral once and removed from undeployed reserve. This
is an internal allocation, not extra account equity or new leveraged exposure.
If the buffer remains unsafe, close with RISK_LIMIT. Risk metadata older than
120 minutes also closes with RISK_LIMIT. No reserve is consumed merely to excuse
stale metadata.

The range stop remains the first observed boundary touch: Long's adverse stop
is the bottom limit, Short's the top, Neutral either. Either range edge closes
at the observed price, without a separate 120-USDT loss stop. Gaps, delayed ticks
and exchange mark-price differences mean liquidation avoidance is not guaranteed.

## Migration and verification

A legacy bot or incompatible leverage closes as PROFILE_UPDATE at an available
observed price before ordinary Core admission can open a new ID. Historical
fills, fees, funding and losses stay in the ledger; no open grid is resized and
no account balance is reset. Historical unknown funding is not reconstructed
with today's rate.

Offline tests cover whole lots, RAY observations, separate Neutral books, matched
pair closes, contract funding evidence and idempotence, adverse-fill admission,
reserve transfer, stale-risk closure and ledger-preserving migration. Deployment
and real KuCoin reconciliation require separate evidence.

Sources: [KuCoin liquidation guide](https://www.kucoin.com/support/26694703491737),
[isolated margin guide](https://www.kucoin.com/support/48142946141508),
[public symbol metadata](https://www.kucoin.com/docs-new/rest/futures-trading/market-data/get-symbol),
[risk tiers](https://www.kucoin.com/docs-new/rest/futures-trading/positions/get-isolated-margin-risk-limit).
