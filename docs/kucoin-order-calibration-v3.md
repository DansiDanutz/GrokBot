# KuCoin order-quantity evidence, v3

Dan authorized inspection of his signed-in Chrome account on 11 September 2026.
The redacted fixture records only strategy parameters and quantities from History
→ Parameters, plus three RAY Order History cycles. Account identifiers, bot IDs,
balances outside these configurations, cookies and credentials are excluded.
No order was opened, stopped or changed. An unsubmitted form-preview attempt
returned inconsistent interval/quantity output and is excluded from calibration.

| Configuration | Grids | Observed lots/order | Contract multiplier | Current used margin | Reserve |
| --- | ---: | ---: | ---: | ---: | ---: |
| HEMI Long 4x | 70 | 114 | 100 | 2500 | 0 |
| BTR Long 4x | 70 | 68 | 10 | 3000 | 0 |
| MOVR Long 6x | 90 | 1698 | 0.1 | 7500 | 0 |
| SOL Long 5x | 99 | 27 | 0.1 | 7000 | 3000 |
| RAY Neutral 5x | 70 | 17 | 1 | 1000 | 200 |
| BTR Long 5x, separate archived bot | 82 | 171 | 10 | 3000 | 2000 |

Multipliers and ticks come from the detached public-market-data copy, so they
remain retrospective contract-specification assumptions for older bots. Long
intervals are inferred from tick-rounded ranges; RAY's 0.0128 interval is directly
observed in its order ladder. KuCoin labels the collateral **Current Margin**;
original creation amounts and subsequent amendments are not established here.

## What is verified

For base quantity `q = lots × multiplier`, a buy at B and sell at S nets
`q × (S − B) − 0.0006 × q × (B + S)`. Leverage is already reflected in order size;
it must not multiply this PnL again. RAY's observed 17-lot cycles reproduce:

| Buy | Sell | Calculated net USDT | KuCoin displayed |
| ---: | ---: | ---: | ---: |
| 1.5992 | 1.6120 | 0.18484576 | 0.1848 |
| 1.5864 | 1.5992 | 0.18510688 | 0.1851 |
| 1.6248 | 1.6376 | 0.18432352 | 0.1843 |

The earlier four Long average profits also fall inside their respective
quantity-based, fee-deducted grid-profit bands. This uses independent observed
quantities; it does not invert profits to invent quantities. It supports the
accounting formula, not a universal allocation formula or liquidation parity.
KuCoin describes its displayed profit/grid as fee-deducted in its
[parameter guide](https://www.kucoin.com/support/21959472633113).

## What remains blocked

Entry-price allocation, upper-price allocation, and upper-price allocation with
an explicit two-fill fee allowance all fail to explain every Long observation.
The upper-price candidates happen to return RAY's 17 lots, but this does not
validate candidates that fail other configurations. RAY was already observed;
it is a retrospective cross-mode check, not an untouched statistical holdout.

KuCoin documents reserve as additional to grid investment and describes margin
additions while a bot runs. It does not publish an allocation equation in the
reviewed [margin guide](https://www.kucoin.com/support/48142946142344).
Those facts justify checking creation and amendment history; they do not prove
that quantities are unchanged after every amendment.

No new quantity rule is adopted. Automatic +1-USDT grid-count recomputation and
its associated expected income remain unvalidated until a rule predicts the
independently observed quantities from known creation inputs. Existing sizing in
v3 candle replays is labeled an uncalibrated sensitivity model, not an executable
KuCoin recommendation. The paper evaluation clock remains unstarted.
