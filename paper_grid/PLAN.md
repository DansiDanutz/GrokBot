# Two-position paper routine

Issue: https://github.com/DansiDanutz/ZmartyChat/issues/104

User scope: daily volatility discovery, top-five analysis, at most two simultaneous
long simulations, bounded larger additional buys, exit at one dollar net on the
whole position, and replacement only when the improvement justifies exit costs.
The user previously selected paper trading first. There is no live authorization.

Implementation sequence:
1. Public KuCoin contract/ticker discovery and completed-hour candle validation.
2. Pure deterministic accounting/risk engine with offline regression tests.
3. Locked atomic local persistence, observable CLI and restart/idempotency tests.
4. Live public-data smoke test, then one paper cycle with genuine current data.
5. Grok Bot daily review and recurring position-management routines calling only
   this CLI, after verification. Native UI schedules remain inactive until then.

Prototype constraints: 1,000 USDT simulated capital; 1x fully collateralized
linear contracts; 200 USDT cost-basis cap per position; entry/add tranches
50/65/85; at most two additions; 1 USD-equivalent target (USDT approximation).
Entry and exit fee assumptions 0.06% each plus modeled adverse slippage and
conservative positive funding accrual. Risk exits may lose money. These are
experimental defaults, not suggested live allocations or a profit guarantee.

Verification: stdlib unittest discovery confined to paper_grid, compile checks,
real public endpoint smoke, persisted first cycle, repeated-run/restart tests,
native routine trigger/activation inspection and a test run through Grok Bot.
Do not run the existing application's startup hooks or legacy trading tests.

Known limits to communicate: polling can miss intrainterval targets/stops; modeled
fills/funding are not exchange fills; unvalidated ranking is a research heuristic;
cash is valid when fewer than two candidates pass; native KuCoin bots and a custom
order engine are different systems; no credentials, Supabase writes or real orders.
