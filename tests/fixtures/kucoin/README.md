# Recorded public KuCoin fixtures

Anonymous public GET samples collected on 2026-09-11, at least two seconds apart.
Each `*-metadata.json` records its public URL, retrieval time and rate headers.
`contracts.json` contains two unmodified contract records from a larger response;
its `original_count` records the observed count. No accounts or credentials were
used. Unit tests read local files and never repeat requests.

Classic futures candle order is open-time milliseconds, open, high, low, close,
volume in contracts, turnover in USDT. Ticker/book `ts` is nanoseconds. These
samples demonstrate the protocol, not investment results or complete history.
