"""Normalize current public snapshots without inventing event timestamps."""
import json

from trader.data.kucoin_public import MAX_CANDLES, number
from trader.data.validation import validate
from trader.data.updater_runtime import epoch_ms

MINUTE_MS = 60000
HOUR_MS = 3600000
DAY_MS = 24 * HOUR_MS
INITIAL_MINUTES = 5
MAX_PAGES = 2
MAX_CANDLE_SLOTS = MAX_CANDLES
WORKER_COUNT = 4
UPDATER_WEIGHT_RATE = 30
SOURCE_CANDLES = 'updater-klines'
SOURCE_FUNDING = 'updater-funding'
TABLES = ('klines', 'funding', 'open_interest', 'top_of_book',
          'ticker_snapshots', 'checkpoints', 'data_quality')


def empty_batch():
    return {table: [] for table in TABLES}


def quality(name, symbol, interval, start, end, checked, status, details):
    return dict(check_name=name, symbol=symbol, interval=interval,
                start_ms=start, end_ms=end, checked_at_ms=checked,
                status=status,
                details_json=json.dumps(details, sort_keys=True))


def checkpoint(source, symbol, interval, frontier, observed):
    return dict(source=source, symbol=symbol, interval=interval,
                next_time_ms=frontier, updated_at_ms=observed)


def snapshot(contract, observed):
    fields = {'last': 'lastTradePrice', 'mark_price': 'markPrice',
              'index_price': 'indexPrice', 'volume_24h': 'volumeOf24h',
              'turnover_24h': 'turnoverOf24h', 'open_interest': 'openInterest',
              'funding_rate': 'fundingFeeRate'}
    row = {name: number(contract[source]) for name, source in fields.items()}
    row.update(symbol=contract['symbol'], time_ms=observed,
               observed_at_ms=observed, source_time_ms=None,
               raw_json=json.dumps(contract, sort_keys=True, allow_nan=False))
    validate('ticker_snapshots', row)
    return row


def open_interest(contract, observed):
    row = dict(symbol=contract['symbol'], time_ms=observed,
               observed_at_ms=observed, source_time_ms=None,
               open_interest=number(contract['openInterest']))
    validate('open_interest', row)
    return row


def top_book(raw, observed):
    bid, bid_size = raw['bids'][0]
    ask, ask_size = raw['asks'][0]
    row = dict(symbol=raw['symbol'], time_ms=epoch_ms(raw['ts']),
               observed_at_ms=observed, bid=number(bid, positive=True),
               ask=number(ask, positive=True), bid_size=number(bid_size),
               ask_size=number(ask_size))
    validate('top_of_book', row)
    return row
