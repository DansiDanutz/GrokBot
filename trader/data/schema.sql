-- Schema version 1. Store applies all statements and user_version atomically.
-- Times are UTC milliseconds; candles are interval opening timestamps.
CREATE TABLE klines (
    symbol TEXT NOT NULL,
    interval TEXT NOT NULL CHECK(interval IN ('1m', '1h')),
    time_ms INTEGER NOT NULL CHECK(time_ms >= 0),
    open REAL NOT NULL CHECK(open > 0),
    high REAL NOT NULL CHECK(high > 0),
    low REAL NOT NULL CHECK(low > 0),
    close REAL NOT NULL CHECK(close > 0),
    volume REAL NOT NULL CHECK(volume >= 0),
    turnover REAL NOT NULL CHECK(turnover >= 0),
    PRIMARY KEY(symbol, interval, time_ms),
    CHECK(high >= open AND high >= close AND high >= low),
    CHECK(low <= open AND low <= close),
    CHECK(time_ms % CASE interval WHEN '1m' THEN 60000 ELSE 3600000 END = 0)
) WITHOUT ROWID;
CREATE TABLE funding (
    symbol TEXT NOT NULL,
    time_ms INTEGER NOT NULL CHECK(time_ms >= 0),
    rate REAL NOT NULL,
    period_ms INTEGER CHECK(period_ms > 0),
    PRIMARY KEY(symbol, time_ms)
) WITHOUT ROWID;
CREATE TABLE open_interest (
    symbol TEXT NOT NULL,
    time_ms INTEGER NOT NULL CHECK(time_ms >= 0),
    observed_at_ms INTEGER NOT NULL CHECK(observed_at_ms >= 0),
    source_time_ms INTEGER CHECK(source_time_ms >= 0),
    open_interest REAL NOT NULL CHECK(open_interest >= 0),
    PRIMARY KEY(symbol, time_ms)
) WITHOUT ROWID;
CREATE TABLE top_of_book (
    symbol TEXT NOT NULL,
    time_ms INTEGER NOT NULL CHECK(time_ms >= 0),
    observed_at_ms INTEGER NOT NULL CHECK(observed_at_ms >= 0),
    bid REAL NOT NULL CHECK(bid > 0),
    ask REAL NOT NULL CHECK(ask >= bid),
    bid_size REAL NOT NULL CHECK(bid_size >= 0),
    ask_size REAL NOT NULL CHECK(ask_size >= 0),
    PRIMARY KEY(symbol, time_ms)
) WITHOUT ROWID;
CREATE TABLE coinglass_liquidations (
    symbol TEXT NOT NULL,
    exchange TEXT NOT NULL,
    time_ms INTEGER NOT NULL CHECK(time_ms >= 0 AND time_ms % 3600000 = 0),
    long_usd REAL NOT NULL CHECK(long_usd >= 0),
    short_usd REAL NOT NULL CHECK(short_usd >= 0),
    PRIMARY KEY(symbol, exchange, time_ms)
) WITHOUT ROWID;
CREATE TABLE ticker_snapshots (
    symbol TEXT NOT NULL,
    time_ms INTEGER NOT NULL CHECK(time_ms >= 0),
    observed_at_ms INTEGER NOT NULL CHECK(observed_at_ms >= 0),
    source_time_ms INTEGER CHECK(source_time_ms >= 0),
    last REAL NOT NULL CHECK(last > 0),
    mark_price REAL NOT NULL CHECK(mark_price > 0),
    index_price REAL NOT NULL CHECK(index_price > 0),
    volume_24h REAL NOT NULL CHECK(volume_24h >= 0),
    turnover_24h REAL NOT NULL CHECK(turnover_24h >= 0),
    open_interest REAL NOT NULL CHECK(open_interest >= 0),
    funding_rate REAL NOT NULL,
    raw_json TEXT NOT NULL,
    PRIMARY KEY(symbol, time_ms)
) WITHOUT ROWID;
CREATE TABLE checkpoints (
    source TEXT NOT NULL,
    symbol TEXT NOT NULL,
    interval TEXT NOT NULL CHECK(interval IN ('1m', '1h')),
    next_time_ms INTEGER NOT NULL CHECK(next_time_ms >= 0),
    updated_at_ms INTEGER NOT NULL CHECK(updated_at_ms >= 0),
    PRIMARY KEY(source, symbol, interval)
) WITHOUT ROWID;
CREATE TABLE universe (
    symbol TEXT PRIMARY KEY NOT NULL,
    updated_at_ms INTEGER NOT NULL CHECK(updated_at_ms >= 0),
    first_candle_ms INTEGER CHECK(first_candle_ms >= 0),
    listed_at_ms INTEGER CHECK(listed_at_ms >= 0),
    turnover_30d REAL CHECK(turnover_30d >= 0),
    atr_pct REAL CHECK(atr_pct >= 0),
    multiplier REAL NOT NULL CHECK(multiplier > 0),
    lot_size INTEGER NOT NULL CHECK(lot_size > 0),
    active INTEGER NOT NULL CHECK(active IN (0, 1)),
    coverage_json TEXT NOT NULL
) WITHOUT ROWID;
CREATE TABLE data_quality (
    check_name TEXT NOT NULL,
    symbol TEXT NOT NULL,
    interval TEXT NOT NULL CHECK(interval IN ('1m', '1h', '5m')),
    start_ms INTEGER NOT NULL CHECK(start_ms >= 0),
    end_ms INTEGER NOT NULL CHECK(end_ms >= start_ms),
    checked_at_ms INTEGER NOT NULL CHECK(checked_at_ms >= 0),
    status TEXT NOT NULL CHECK(status IN ('pass', 'warn', 'fail', 'unknown')),
    details_json TEXT NOT NULL,
    PRIMARY KEY(check_name, symbol, interval, start_ms, end_ms)
) WITHOUT ROWID;
