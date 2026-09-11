# Revised KuCoin paper policy

Dan confirmed a total bankroll of **2,400 USDT**: up to two KuCoin Futures
Grid bots, each with **1,000 used margin + 200 reserve**, at **fixed 5x**.
Reserve does not purchase another 1,000 USDT of leveraged exposure. Capital
cannot be silently increased to replace a losing bot.

Radar defaults to ten candidates, configurable from five to ten. The actual
KuCoin Volatility definition remains unverified; a public high/low amplitude
must stay labeled as a proxy. Evaluate entry, direction, support/resistance,
net +1-USDT grid floor, liquidity and liquidation protection before admission.
Start the best eligible setup, then a different second candidate.

A challenger may replace the worse incumbent when it has a higher expected
grid rate and positive projected grid-income advantage after executable close
costs and new opening fees over the declared horizon. Use at most one ordinary
switch each hour; preserve both rates, all costs, capital feasibility and the
reason. Expected future grid income is an estimate, never a profit guarantee.
Risk-protection exits do not wait for a replacement candidate.

Default hard exits are 5% beyond either range edge. If current inventory or
a monotonic fill projection cannot safely reach the stop, tighten that side
to 1%; do not loosen it later. The model requires an additional 1%-of-edge
clearance for maintenance and modeled close fees. If 1% is also unsafe or has
already been crossed, close immediately. Gaps may still cause liquidation;
the ledger must report that instead of claiming the stop guaranteed safety.
No automatic capital contribution beyond the 200 reserve is permitted.

The **new 48-hour paper evaluation starts only when the revised paper runner
actually starts** and has its first fresh market observation. Preparing source,
a schedule or an offline replay must not start the countdown. Preserve older
run data and IDs. Repeated start events must not reset an active clock. The
48-hour boundary requests an audit; it does not stop the strategy for profit.

This follow-up is isolated from PR #14's frozen head. Existing runtime,
LaunchAgents, v1, Phase 2, Telegram, credentials and prior timers remain
untouched. No deployment or live orders are performed by source changes.
All earlier calibration/holdout gaps remain blockers to live recommendation.
