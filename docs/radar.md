# KuCoin grid radar

The radar is a read-only operator aid. It reads the existing KuCoin market SQLite database, prints ranked tables, and writes `radar.json`. It cannot place, edit, or stop an exchange order. Dan enters any selected setup in KuCoin by hand.

## Run it

```sh
cd ~/ZCodeProject/GrokBot
python3 -m trader.radar \
  --database ~/Sandbox/grokbot/market-data/phase-2-20260911/market.sqlite3 \
  --json ~/Sandbox/grokbot/radar/radar.json
```

Open `/radar` on the existing paper server. The publisher includes the same page at `/radar` and copies a bounded public DTO to `/data/radar.json`.

The uninstalled template `config/launchd/com.danslab.trader-radar.plist.example` runs at minute `05` each hour. Before considering installation, replace `REPLACE_WITH_DAN_CHAT_ID` and manually run its wrapped command once. The wrapper reads `DLS_TELEGRAM_BOT_TOKEN` from `~/.config/danslab/credentials/telegram.json`; the token never belongs in source or a plist. Telegram sends the top three per section only when their symbol identities change.

## Columns and sections

- **Direction:** `LONG` when 4-hour and daily EMA20/50 agree upward, `SHORT` when both agree downward, `TURNING-UP` or `TURNING-DOWN` when only the 4-hour trend has turned, otherwise `NEUTRAL`.
- **ATR 1h %:** 14-period hourly average true range divided by current price. It represents oscillation, not a forecast.
- **Range:** directional candidates use current price, 4-hour ATR, and the 7-day extreme. Neutral candidates use the 7-day low and high.
- **Step % / grids:** the arithmetic grid interval and the range width divided by that interval, capped at 200.
- **Expected grids/h:** the fixed coefficient times hourly ATR% divided by step%. Ranking multiplies this estimate by liquidity, capped at full weight from 8 million USDT turnover.

The output shows Majors (BTC, ETH, SOL direction only), Turning up, Turning down, Long, Short, Neutral candidates in the middle 25–75% of their 7-day range, and Movers with an absolute 24-hour move above 15%. Each section is capped at eight rows. Candidate sections require at least 3 million USDT 24-hour turnover, spread at or below 0.15%, and seven days of listing age.

## Fixed live-bot constants

These are named constants in `trader/radar/radar.py`; there is no fitting, replay, or research code:

| Turnover | Step | k | Expected grids/h |
| --- | ---: | ---: | --- |
| below 50M USDT | 0.80% | 1.90 | `1.90 × ATR1h% / 0.80` |
| 50M USDT or more | 0.52% | 0.45 | `0.45 × ATR1h% / 0.52` |

Dan fitted these constants from the live HEMI, BTR, MOVR, and SOL grid bots observed on 11 September 2026. They are operational heuristics and expected values, not guaranteed fills or profit.
