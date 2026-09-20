# Active paper entry upgrade: funding 4h and confirmed 1h retest

Authorized by Dan on 20 September 2026. Version `funding4h_retest1h_v1`.
Production radar CLI and paper daemon require this version for new entries.
Pure analysis/policy APIs retain explicit legacy operation for existing fixtures
and comparisons; stale/unversioned radar cannot open a production bot.

- Use confirmed hourly support/resistance only; choose the narrowest supported
  feasible pair, then the maximum passing count, 70–200 grids. Never invent or
  widen a level. Preserve strict Long 40% buy/60% sell, Short 60/40, Neutral
  50/50, with the existing one-order tolerance and risk/lot checks.
- Preserve 1,000 USDT allocation, 200 reserve and 5x. Adjacent completed-pair
  modeled margin return must exceed 1% after both 0.06% fills and adverse
  funding. Budget ceil(4h / the recorded funding interval) settlements, valued
  at the upper bound; ignore receipts. This conservative phase-independent
  scenario is not realized funding or a guarantee if the rate/hold changes.
  Missing or older-than-two-hours rate/interval metadata rejects admission.
- Entry confirmation: the latest stored closed 1h candle must retest a previously
  confirmed internal support (Long) or resistance (Short), with at least two
  recorded pivot confirmations before that candle began. The preceding hour
  must be contiguous and on the expected side. Touch tolerance is min(0.25 ATR1h,
  0.5% price); the candle must hold that zone and close away from it, bullish
  for Long or bearish for Short. Neutral accepts either rejection, while retaining
  its middle-range and split gates. The retest level may be inside the wider
  selected trading range, so entry confirmation does not move the range edges.
- No chasing: current price remains on the confirmed side and within 0.25 ATR1h
  of the confirmation close. Long must remain below the preceding 24h high,
  Short above its low. Confirmation expires two hours after its close, matching
  the collector's bounded lag. Partial/open candles never confirm an entry.
- Radar saves the version, actual funding scenario and retest evidence/reasons.
  Admission rechecks age, funding return, direction and live-price constraints.
  The opened position's setup dossier freezes the receipt; later policy changes
  never relabel earlier entries or force a profile update.

Activation preserves existing bots, start time, bankroll and equity/history.
Existing pinned stop/range rules continue. No real orders, resets, replay,
backtest, manual Telegram send or installed LaunchAgent changes.
