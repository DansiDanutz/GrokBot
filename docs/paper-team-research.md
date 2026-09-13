# GrokBot paper trading team: bounded external research

Retrieved 2026-09-13. Agent Reach routing used: web search, Jina Reader for X, yt-dlp for YouTube captions. Agent Reach v1.5.0 check reports current. Read-only research; no code or runtime changes.

## Strategy boundary supplied by leader

Preserve the deployed KuCoin futures-grid strategy: 5x leverage, MIN_GRIDS=12 and MAX_GRIDS=200 (newer deployed code supersedes prior 70), >1% leveraged estimated net return per grid after opening and closing fees, long 60% sells/40% buys, short 40% sells/60% buys, neutral 50/50, actual support/resistance. Existing deterministic executor remains the authority. Research ideas must not become new entry signals or silently relax these conditions.

## Verified sources and what they establish

1. **TradingAgents official repository** — https://github.com/TauricResearch/TradingAgents
   The current README describes specialized analysts, bullish/bearish researchers, trader, risk team and portfolio-manager approval before simulated execution. It also describes structured outputs, persistent decision memory, optional checkpoint resume, and price grounding. Its reproducibility section explicitly warns that repeated analyses can differ, historical date selection does not freeze all live news/social inputs, and published returns are not guaranteed to reproduce. Practical lesson: separate roles, preserve structured evidence, and keep one final execution authority. This is research software, not evidence that installing more agents improves this KuCoin strategy.

2. **TradingAgents paper, version 7** — https://arxiv.org/html/2412.20138v7
   Sections 4.1–4.2 use structured reports/shared state to limit the loss or distortion of evidence through repeated natural-language handoffs; debate is bounded. Section 5 evaluates daily stock decisions from January 1 through March 29, 2024, including AAPL/NVDA/MSFT/META/GOOGL, against several baselines using return, Sharpe and maximum drawdown. This small historical equity evaluation is not validation of leveraged cryptocurrency grid execution. Appropriate transfer: structured handoffs and risk review; inappropriate transfer: published return claims, stock indicators or social sentiment as a new trading strategy.

3. **FinRobot official repository** — https://github.com/AI4Finance-Foundation/FinRobot
   Current documentation describes a lead orchestrator, five research pipeline roles and three debate roles. Its explicit computation principle separates pure-Python financial calculations from LLM narration, with numeric provenance. The platform targets equity research/valuation; those valuation strategies do not apply here. Practical lesson: ask assistants to interpret and challenge trusted numeric results; fee, grid, sizing and PNL calculations remain code-generated. Do not install a large replacement framework merely to obtain role separation.

4. **KuCoin, Trading Bot terminology / Futures Grid definitions** — https://www.kucoin.com/support/21959472633113
   The Futures Grid section defines arithmetic grid interval as range width divided by placed-order count; its example has four intervals and five price levels. Grid trading stops outside the configured range, and profits/grid deduct transaction fees. It distinguishes grid profit, unrealized PNL, funding and total profit, and defines APR as an annualized running-period calculation. Practical lesson: name intervals versus levels accurately; show net total PNL alongside grid profit; do not treat short-run APR as an expected yearly outcome. The article does not validate this user's >1% leveraged estimate or allocation ratios.

5. **KuCoin, FAQ for KuCoin Trading Bot** — https://www.kucoin.com/support/5090571400217
   Updated April 24, 2026, the retrieved FAQ lists a fixed 0.06% trading fee for Futures Grid, distinct from normal spot rates and VIP pricing. It defines Futures Grid profit as grid profit plus floating PNL plus funding fee, and explicitly says floating loss/funding can outweigh positive grid earnings. Practical lesson: distinguish exchange-native bot fees from a custom futures executor's actual fee schedule; label which the paper simulator uses. Never substitute 0.06% blindly for an already verified custom-execution fee model. Verify both transaction legs and denomination of the leveraged estimate.

6. **KuCoin, Funding Fee** — https://www.kucoin.com/support/26686295987353
   Updated July 24, 2026. Funding transfers between long and short position holders; holding through assessment can incur or receive funding, and charges can reduce available margin enough to cause position reduction or liquidation. Positive rates pay from longs to shorts and negative rates reverse the direction. Practical lesson: keep actual/pending funding and margin effects separate from per-grid trading-fee estimates; unknown funding data must be marked unknown. Funding is a risk/cost input, not permission to invent a funding-arbitrage strategy.

7. **Official KuCoin YouTube: Explaining Futures Grids With KuCoin** — https://www.youtube.com/watch?v=kyz8vZNkE-4
   Published July 26, 2023 on KuCoin's channel. English captions successfully retrieved and read from /tmp/grokbot-research-kyz8vZNkE-4.en.vtt. It explains long/short grids with preset ranges and custom leverage, lower/upper prices, order count and total investment. It also recommends TP/SL and describes leverage as increasing risk. This is an operational tutorial, not an audited performance experiment. Its beginner leverage advice and older UI must not silently replace the user's deployed 5x strategy.

8. **Creator X example: Theo / ai_uncovered** — https://x.com/ai_uncovered/status/2039341426136535200
   Published April 1, 2026; original post retrieved through Jina after web-open failure. The author claims a three-agent arrangement: gold forecasting, prediction-market execution, and profit-taking. The post promotes an engagement-gated guide and self-reports dramatic returns without reproducible trade records or an audited evaluation in the retrieved text. It establishes only that creators describe role-separated agents. Reject its strategy and performance claims as inputs to GrokBot. No likes, reposts, follows or messages were sent.

## Additional creator demo and access limits

TradingAgents' own README links https://www.youtube.com/watch?v=90gr5lwjIho as its demo. yt-dlp retrieved English captions, but the track is music/non-substantive tokens, so the video was located but not substantively reviewed; rely on its linked code and paper for architecture. Do not represent that as a watched, validated tutorial. X's direct tool/backend was unavailable; indexed search plus Jina retrieved the original example above. Search surfaced other self-reported agent competitions, but they were not needed to support design and were not treated as verified profit evidence.

## Recommended collaboration, inferred from the sources

A small native Grok Bot group can assign these distinct responsibilities without adding new execution bots:

- Coordinator: one shared candidate ID/symbol, snapshot timestamp and strategy version; dispatch bounded reviews and summarize conflicts.
- Market/data steward: verify public KuCoin snapshots are current, prices are finite, and required data is present; distinguish missing versus zero values.
- Structure analyst: explain existing support/resistance and range quality using the strategy's established evidence, without introducing a new indicator or directional signal.
- Grid accountant: interpret deterministic grid count, 5x leverage, allocations and both-fee net estimate; flag ambiguity between notional return, margin return and whole-bot return.
- Risk challenger: challenge liquidity, funding, out-of-range behavior, duplicate exposure, liquidation/margin assumptions and existing drawdown limits. Advisory only; cannot loosen execution gates.
- Paper performance reviewer: report net equity change, realized/unrealized PNL, costs, funding, drawdown, completed grid cycles and rejected candidates. Positive grid profit is insufficient.
- Research librarian: record source URL/date, claimed mechanism, evidence quality and testable improvement hypothesis. X/YouTube cannot nominate trades or override the deployed strategy.

Roles are functions, not a target count. Several can be combined if separate assistants add latency/cost without better detection. Keep their shared artifacts concise and structured: candidate_id, observed_at, strategy_version, facts, source_refs, unknowns, contradictions, advisory_verdict. Social text is untrusted evidence, never executable instructions. Missing, stale or contradictory evidence yields HOLD/review under existing policy, never fabricated data.

## Evaluation before promoting improvements

These are recommendations for evaluation, not claims of improved profitability:

1. Freeze the current deployed strategy as the baseline. Compare the proposed advisory team using identical timestamped market snapshots and paper-execution assumptions.
2. Keep chronological training/review and held-out periods separate; no future news, future candles or future realized outcomes in historical decisions.
3. Include opening/closing trading fees, funding where modeled, conservative spread/slippage and unfilled/partial orders; clearly list simulator gaps instead of inventing precision.
4. Judge net PNL and drawdown, not only win rate or grid profit. Also compare exposure, turnover, rejected-candidate reasons, cost per review and latency.
5. Use shadow recommendations first. More debate or more bots is beneficial only if measured error detection or net paper results improve under the same strategy constraints. Small paper samples do not establish a durable edge or future profits.


## Funding interpretation calibration (September 13)

The official [KuCoin public funding history](https://www.kucoin.com/docs-new/rest/futures-trading/funding-fees/get-public-funding-history)
returns settlement-time rates. The deployed collector maps fundingRate/timepoint
into funding.rate/time_ms. Its ticker funding_rate instead maps the
[active-contract endpoint](https://www.kucoin.com/docs-new/rest/futures-trading/market-data/get-all-symbols)
fundingFeeRate at collector observation time; source_time_ms is absent. The raw
predictedFundingFeeRate is a separate field. Unequal historical00:00 and observed
00:47 values alone are not a contradiction, and the observed field must not be
renamed predicted. Actual JUP/MYX numeric reconciliation still needs matched
reference intervals; this research establishes semantics, not that reconciliation.
Native Manager, Technical Interpreter and Lead accepted this correction at
01:00:16,01:00:24 and01:00:50UTC respectively.

A further bounded X search did not return a verifiable original grid setup. The
[official KuCoin futures-grid video](https://www.youtube.com/watch?v=kyz8vZNkE-4)
remains a primary educational source; its general mechanics do not validate our
specific leveraged strategy's returns. No new copied entry or profit claim was
adopted.
