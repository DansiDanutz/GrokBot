# Learning lab

This extension uses the existing paper research design tokens, system fonts, bordered cards and responsive navigation. It adds an evidence section immediately after the equity history. The baseline and liquidation-filter accounts are always selected independently; shared market checks are explicitly labeled and never counted twice.

## Reading order

1. Choose all history, the last 24 hours or the last seven days, then one paper account.
2. Read the funnel: successful checks, distinct shortlist coins, eligible observations, initial fills, additional fills and closed outcomes.
3. Compare closed net P&L with marked equity change, costs, coverage and drawdown.
4. Inspect daily activity and closed P&L charts, outcome counts and closing reasons.
5. Compare zero/one/two-add cohorts, individual coins and closed-position journal evidence.
6. Read measurement definitions and limits before forming a testable hypothesis.

## Charts and accessibility

Charts use inline SVG with descriptive accessible labels. Daily series have expandable exact-value tables. Outcome counts are included in the accessible label; exit reasons have a visible value table. Positive, negative and zero values have explicit text, so meaning never depends on color. Controls use native labeled selects, keyboard focus and normal tab order. Wide tables scroll inside cards. No assets, dependencies or trading controls are introduced.

## Data contract

The local page fetches `/api/analytics`; the public page fetches `/data/analytics.json`. Analytics refresh runs independently alongside the original portfolio and report archive requests, with a separate overlap guard and 12-second timeout. Refresh failure preserves prior evidence and adds a status warning. Snapshot age beyond 45 minutes is disclosed. Analytics DTO `schema: 1` provides three windows and separate account aggregates; the frontend does not infer missing historical records or sum account results.

Real zeros remain zero. Missing numbers use an em dash; missing lifecycle information uses Unknown. No completed trades is explicitly described as insufficient evidence. Journals disclose their 200-row cap, daily charts their 90-day cap and coin tables their 100-coin cap. Summary totals are provided by the backend before truncation. Daily buckets are UTC; position and snapshot timestamps are Europe/Bucharest.

The learning questions are fixed prompts for later audits. Neither this page nor its hypotheses modify strategies or place exchange orders. Parent-task browser verification covers visual layout and actual endpoint integration before deployment.
