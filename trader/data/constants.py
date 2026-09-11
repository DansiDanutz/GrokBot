"""Storage contract constants; UTC milliseconds and quote-currency turnover."""

SCHEMA_VERSION = 1
BUSY_TIMEOUT_MS = 5000
PRIVATE_DIRECTORY_MODE = 0o700
PRIVATE_FILE_MODE = 0o600
MAX_TIMESTAMP_MS = 253402300799999
MAX_TEXT_LENGTH = 128
MAX_JSON_LENGTH = 1000000
INTERVAL_MS = {"1m": 60000, "1h": 3600000}
SIDECAR_SUFFIXES = ("-wal", "-shm", "-journal")

# column name, validation kind. All fields required; '?' permits explicit None.
TABLE_COLUMNS = {
    "klines": (
        ("symbol", "text"),
        ("interval", "interval"),
        ("time_ms", "timestamp"),
        ("open", "positive"),
        ("high", "positive"),
        ("low", "positive"),
        ("close", "positive"),
        ("volume", "nonnegative"),
        ("turnover", "nonnegative"),
    ),
    "funding": (
        ("symbol", "text"),
        ("time_ms", "timestamp"),
        ("rate", "finite"),
        ("period_ms", "positive_integer?"),
    ),
    "open_interest": (
        ("symbol", "text"),
        ("time_ms", "timestamp"),
        ("observed_at_ms", "timestamp"),
        ("source_time_ms", "timestamp?"),
        ("open_interest", "nonnegative"),
    ),
    "top_of_book": (
        ("symbol", "text"),
        ("time_ms", "timestamp"),
        ("observed_at_ms", "timestamp"),
        ("bid", "positive"),
        ("ask", "positive"),
        ("bid_size", "nonnegative"),
        ("ask_size", "nonnegative"),
    ),
    "coinglass_liquidations": (
        ("symbol", "text"),
        ("exchange", "text"),
        ("time_ms", "timestamp"),
        ("long_usd", "nonnegative"),
        ("short_usd", "nonnegative"),
    ),
    "ticker_snapshots": (
        ("symbol", "text"),
        ("time_ms", "timestamp"),
        ("observed_at_ms", "timestamp"),
        ("source_time_ms", "timestamp?"),
        ("last", "positive"),
        ("mark_price", "positive"),
        ("index_price", "positive"),
        ("volume_24h", "nonnegative"),
        ("turnover_24h", "nonnegative"),
        ("open_interest", "nonnegative"),
        ("funding_rate", "finite"),
        ("raw_json", "json"),
    ),
    "checkpoints": (
        ("source", "text"),
        ("symbol", "text"),
        ("interval", "interval"),
        ("next_time_ms", "timestamp"),
        ("updated_at_ms", "timestamp"),
    ),
    "universe": (
        ("symbol", "text"),
        ("updated_at_ms", "timestamp"),
        ("first_candle_ms", "timestamp?"),
        ("listed_at_ms", "timestamp?"),
        ("turnover_30d", "nonnegative?"),
        ("atr_pct", "nonnegative?"),
        ("multiplier", "positive"),
        ("lot_size", "positive_integer"),
        ("active", "flag"),
        ("coverage_json", "json"),
    ),
    "data_quality": (
        ("check_name", "text"),
        ("symbol", "text"),
        ("interval", "quality_interval"),
        ("start_ms", "timestamp"),
        ("end_ms", "timestamp"),
        ("checked_at_ms", "timestamp"),
        ("status", "status"),
        ("details_json", "json"),
    ),
}

PRIMARY_KEYS = {
    "klines": ("symbol", "interval", "time_ms"),
    "funding": ("symbol", "time_ms"),
    "open_interest": ("symbol", "time_ms"),
    "top_of_book": ("symbol", "time_ms"),
    "coinglass_liquidations": ("symbol", "exchange", "time_ms"),
    "ticker_snapshots": ("symbol", "time_ms"),
    "checkpoints": ("source", "symbol", "interval"),
    "universe": ("symbol",),
    "data_quality": ("check_name", "symbol", "interval", "start_ms", "end_ms"),
}
QUALITY_STATUSES = ("pass", "warn", "fail", "unknown")
QUALITY_INTERVALS = (*INTERVAL_MS, "5m")
