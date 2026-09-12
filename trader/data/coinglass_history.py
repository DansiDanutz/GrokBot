"""Persist CoinGlass aggregated liquidation history for later research.

Public provider data only; never authentication or exchange orders. Rows are
completed aggregate-exchange hourly liquidation USD (Binance, OKX, Bybit),
not KuCoin execution data and not prospective liquidation clusters. The
table exists so cluster research has an accumulating local history; the
live entry filter in paper_grid.coinglass is unchanged.
"""
import argparse
import json
import math
import sys

from paper_grid.coinglass import (
    CoinGlassError, MAX_SYMBOLS, read_api_key, request_history, underlying,
)
from trader.autopilot.storage import read_json
from trader.data.store import Store
from trader.data.updater_records import HOUR_MS, quality
from trader.data.updater_runtime import AlreadyRunning, CollectorLock, epoch_ms

EXCHANGE_LABEL = 'Binance,OKX,Bybit'
MAX_ROWS_PER_SYMBOL = 49


def _symbol_rows(symbol, rows, now_ms):
    """Completed hourly rows only; tolerate individual bad rows."""
    kept, rejected = [], 0
    for row in rows:
        try:
            stamp = epoch_ms(row['time'])
            long_usd = float(row['aggregated_long_liquidation_usd'])
            short_usd = float(row['aggregated_short_liquidation_usd'])
        except (KeyError, TypeError, ValueError, OverflowError):
            rejected += 1
            continue
        if stamp % HOUR_MS or stamp + HOUR_MS > now_ms \
                or not math.isfinite(long_usd) or not math.isfinite(short_usd) \
                or long_usd < 0 or short_usd < 0:
            rejected += 1
            continue
        kept.append(dict(symbol=symbol, exchange=EXCHANGE_LABEL,
                         time_ms=stamp, long_usd=long_usd,
                         short_usd=short_usd))
    return kept[:MAX_ROWS_PER_SYMBOL], rejected


def _snapshot_symbols(path):
    """Prioritize symbols whose current paper outcomes need context."""
    snapshot = read_json(path)
    try:
        groups = (snapshot['open_bots'], snapshot['watchlist']['core'],
                  snapshot['watchlist']['bench'])
    except (KeyError, TypeError):
        raise ValueError('invalid autopilot snapshot') from None
    if not all(isinstance(group, list) for group in groups):
        raise ValueError('invalid autopilot snapshot')
    names = []
    for group in groups:
        for row in group:
            if not isinstance(row, dict):
                raise ValueError('invalid autopilot snapshot')
            symbol = row.get('symbol')
            underlying(symbol)
            names.append(symbol)
    return list(dict.fromkeys(names))


def run(store, symbols, now_ms, *, getter=None, api_key=None, key_path=None):
    """Fetch history for at most nine symbols and upsert idempotently.

    getter(symbol, now_seconds, api_key) returns raw provider rows; offline
    tests inject a fake. Errors are per-symbol and recorded, never fatal to
    the batch. Returns the collection_cycle-style details dict.
    """
    fetch = getter or request_history
    names = list(dict.fromkeys(symbols))[:MAX_SYMBOLS]
    now_s = now_ms // 1000
    liquidations, failures = [], []
    rejected_rows, symbols_ok = 0, []
    try:
        key = api_key if api_key is not None else read_api_key(key_path)
    except CoinGlassError:
        key = None
    for symbol in names:
        try:
            underlying(symbol)
            if key is None:
                raise CoinGlassError('API key unavailable')
            try:
                rows = fetch(symbol, now_s, key)
            except CoinGlassError:
                raise
            except Exception:
                raise CoinGlassError('history request failed') from None
            if not isinstance(rows, list) or len(rows) > MAX_ROWS_PER_SYMBOL:
                raise CoinGlassError('invalid history size')
            kept, rejected = _symbol_rows(symbol, rows, now_ms)
            rejected_rows += rejected
            if not kept:
                raise CoinGlassError('no valid completed history')
            liquidations.extend(kept)
            symbols_ok.append(symbol)
        except CoinGlassError as error:
            failures.append(dict(symbol=symbol, errors=[str(error)]))
    stamps = [row['time_ms'] for row in liquidations]
    status = ('fail' if not symbols_ok else
              'warn' if failures or rejected_rows else 'pass')
    details = dict(status=status, symbols_requested=len(names),
                   symbols_ok=len(symbols_ok), rows=len(liquidations),
                   rejected_rows=rejected_rows, failures=failures,
                   completed_hours_covered=bool(stamps),
                   source='coinglass_aggregated_history',
                   paper_only=True)
    checked = now_ms
    record = quality('coinglass_history', '*', '1h',
                     min(stamps, default=now_ms), max(stamps, default=now_ms),
                     checked, status, details)
    with store.transaction():
        store.upsert('coinglass_liquidations', liquidations)
        store.upsert('data_quality', [record])
    return details


def _active_symbols(store):
    rows = store.query(
        "SELECT u.symbol FROM universe AS u WHERE u.active=1 "
        "ORDER BY COALESCE(u.turnover_30d, ("
        "SELECT t.turnover_24h FROM ticker_snapshots AS t "
        "WHERE t.symbol=u.symbol ORDER BY t.time_ms DESC LIMIT 1), -1) DESC, "
        "u.symbol")
    return [row['symbol'] for row in rows]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', required=True)
    parser.add_argument('--once', action='store_true', required=True)
    parser.add_argument('--symbols', help='comma-separated override, '
                        'otherwise top active universe by turnover')
    parser.add_argument('--autopilot-snapshot',
                        help='prioritize open bots and the current watchlist')
    parser.add_argument('--secrets-file', default=None,
                        help='CoinGlass API key file')
    args = parser.parse_args(argv)
    try:
        with CollectorLock(args.database, suffix='.coinglass.lock'), \
                Store(args.database) as store:
            if args.symbols:
                symbols = [name for name in args.symbols.split(',') if name]
            else:
                priority = (_snapshot_symbols(args.autopilot_snapshot)
                            if args.autopilot_snapshot else [])
                symbols = list(dict.fromkeys(priority + _active_symbols(store)))
            if not symbols:
                raise ValueError('no symbols to record')
            details = run(store, symbols, int(store.query(
                "SELECT CAST(strftime('%s','now') AS INTEGER)*1000 AS now_ms"
            )[0]['now_ms']), key_path=args.secrets_file)
            print(json.dumps(details, sort_keys=True))
            return 0 if details['status'] != 'fail' else 1
    except AlreadyRunning as error:
        print(str(error), file=sys.stderr)
        return 2
    except Exception as error:
        print('coinglass recorder failed: ' + type(error).__name__,
              file=sys.stderr)
        return 1
