# Paper liquidation and 5× profile

All directions use 1,000 USDT deployed margin at 5×, plus a separately allocated
200 USDT reserve. Five bots allocate 6,000 USDT from the 10,000 USDT account,
not 30,000 USDT of cash. Reserve is not leveraged or automatically transferred.
The paper engine stops at the first observed range-boundary hit; the stop-loss is the adverse range boundary, with no separate 120 USDT
net-loss trigger. Range hits close on that update at the observed price.
A gap or missing feed can cross these thresholds; no absence-of-liquidation claim
is made. Existing nonmatching profiles close as PROFILE_UPDATE before ordinary
Core admission opens replacement bots. Historical fees and losses remain.

## Estimate shown on the card

This engine holds signed net base quantity q, not simultaneous hedge legs.
It is an isolated/net-position estimate, not the KuCoin Futures Grid hedge quote.
With entry E, deployed collateral C = margin + realized PnL - fees - funding,
and r = maintenance-margin rate + assumed liquidation-fee rate:

    liquidation price = (q * E - C) / (q - abs(q) * r)

The hypothetical reserve scenario substitutes C + reserve; it does not transfer
reserve or double-count it in account equity. Flat inventory has no price. A
nonpositive result for a long is labeled no positive price under this model.
Current position quantity is already in base units; do not multiply it again
by the exchange contract multiplier. Future grid fills change this estimate.

The maintenance rate and lowest-tier cap come from the latest per-symbol
ticker_snapshots.raw_json already recorded by the public collector. Reject data
over 120 minutes old or a tier whose cap cannot cover the opening value and
either estimated liquidation notional. Missing/invalid parameters remain explicit.
Liquidation fee is assumed 0.06% from KuCoin’s worked example, not an authenticated
account-specific rate. Fees/funding already paid reduce deployed collateral.
The exchange uses mark price; this paper bot’s fills and exits use observed ticks.

Sources: [KuCoin liquidation guide](https://www.kucoin.com/support/26694703491737),
[isolated margin guide](https://www.kucoin.com/support/48142946141508),
[public symbol metadata](https://www.kucoin.com/docs-new/rest/futures-trading/market-data/get-symbol),
[risk tiers](https://www.kucoin.com/docs-new/rest/futures-trading/positions/get-isolated-margin-risk-limit).

## Verification

Offline tests cover both signed equations, hypothetical reserve, flat and fully
collateralized states, stale/invalid metadata, insufficient tiers, read-only DB
handling, immediate range exits including gaps and restart candles, same-timestamp
retries, and ledger-preserving replacement of incompatible profiles.
