# Paper research learning lab

The learning lab turns the recorded experiment into descriptive evidence. It does
not tune the strategy, create model training jobs, or execute orders. The two paper
accounts are correlated simulations and must be selected separately.

## What the numbers mean

- **Checks attempted** are recorded observation attempts. Successful checks exclude
  skipped/aborted cycles; timer wakeups without a cycle are not checks.
- **Coins found** means distinct symbols in the saved shortlist during the selected
  period. The latest liquid-contract universe is a separate point-in-time count.
  Reusing a cached discovery snapshot is not a new market discovery scan.
- **Eligible observations** count repeated qualifying symbol checks, not orders,
  unique opportunities or probabilities of winning.
- **Entries** are initial position buys. **Additional buys** are the recorded `add`
  fills after an adverse move plus the existing rebound check. They are not
  separate completed trades. A fill is an entry, add or close.
- **Completed trades** are position closes. Wins have positive net P&L, losses
  negative net P&L, and breakeven is exactly zero. Missing P&L is not zero.
- **Net closed P&L** already includes all entry/exit fees and modeled funding
  attached to those closes. Do not subtract the displayed costs a second time.
  Fees paid within a selected period can relate to positions still open.
- **Positive profit / negative loss** split the net results of winning and losing
  closes. Profit factor divides the former by the absolute latter; zero-loss or
  no-trade samples do not establish infinite profitability.
- **Equity change** uses recorded portfolio marks at the period boundaries. It
  includes open exposure and cost timing, so it differs from closed-trade P&L.
  Mark age and observed drawdown limitations remain explicit.
- **Add cohorts** group closed lifecycles with zero, one or two additional buys.
  Missing original entries are unknown, not silently counted as zero adds.
  Different outcomes between these groups are descriptive, not proof that adding
  caused a recovery or improved returns.
- **Exit reasons** use the recorded trigger, including profit target, position loss
  limit, price stop, daily loss limit and rotation. Deferred exits are not fills.

The daily chart buckets use UTC; the audit scheduler continues to use the
Europe/Bucharest calendar for daily and weekly reports. Window totals are computed
before limiting the displayed journal, daily bars and coin table. The UI discloses
those display limits.

## Evidence and boundaries

`analytics.py` reads the hot experiment and declared history archives without
provider calls or account mutations. It checks archive completeness and merges
exact duplicates before counting. Input limits and invalid records produce
unavailable analytics instead of silently incomplete results.

The local `/api/analytics` route caches successful calculations for 60 seconds.
The Vercel publisher independently exports `/data/analytics.json` every 30 minutes.
Analytics failures leave portfolio reporting usable; the learning panel reports
its own failure and generation timestamp. No raw provider payloads, credentials
or private error messages are included in public analytics.

## Questions to review as evidence accumulates

1. Are many checks rejected because quotes, depth or liquidation data are missing?
2. Do additional buys improve net outcomes, or concentrate losses and extend holds?
3. Which exit reasons account for most losses after all recorded costs?
4. Does the liquidation filter improve net expectancy and drawdown across later,
   unseen periods, rather than merely reducing the number of trades?
5. Do apparent coin-level results persist with more observations and complete data?

Retain losing trades and unknowns. Record proposed rule changes separately and
evaluate them on later paper data before changing the strategy. A dashboard is
an evidence journal; it is not proof of a profitable live-trading strategy.

## Activation — 11 September 2026

The read-only analytics module, HTTP route, local HTML and public exporter were
installed in the existing active checkout. The dashboard service was briefly
reloaded after backing up its source and ledger. State bytes were preserved
through the reload and all four sealed strategy hashes matched. Worker health
returned running without cycle or audit errors.

Local `/api/analytics` and production `/data/analytics.json` both returned HTTP
200 with status `ok`. The first live analytics publication showed 8 successful
checks, 5 shortlisted symbols, 40 symbol-observations and zero trades per account.
These are activation counts, not a present-performance claim. Synthetic chart
fixtures were served only on an isolated local QA port and were never published.

The installed Grok routine now reads the learning endpoint, keeps account/period
comparisons separate, states sample sizes, and preserves the existing strategy.
181 Python tests and 17 Node integration tests passed; browser checks covered
real empty states, isolated positive/negative outcomes, additional-buy cohorts,
period/account filters and readable chart axes.

## Rejection telemetry and per-tick equity

A reviewed telemetry-only upgrade records an explicit boundary in
`experiment.json.telemetry_upgrades`. It preserves accounts, decisions and past
observations. New successful observations have `telemetry_schema: 1` and a
per-account `equity` record with an estimate flag. Existing history is not backfilled.

`buy_rejected` records distinguish execution, selection and rotation-trial gates.
They carry only fixed reasons and finite numeric context. A failed trial creates
no hypothetical fill. These events never increment fills, trades or fees. They
cover the instrumented gates, not every possible scanner/portfolio restriction.

The Learning lab labels rejection coverage complete, partial or unavailable.
Partial periods explicitly count older checks without these logs. Windowed
drawdown now includes available per-tick marks; older periods retain published
marks. Mixed sampling has no invented expected sample count or coverage ratio.
Audit eligibility now calls the same CoinGlass filter as the engine.

See [REPLAY.md](REPLAY.md) for deterministic offline replay from an explicit
historical checkpoint. The first recorded 10-observation replay ran twice
identically and matched both live final account states. That establishes
reproducibility only; the ledger still had no completed trades.
