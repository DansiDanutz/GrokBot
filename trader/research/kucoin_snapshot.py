"""Read an explicitly attested, detached SQLite copy; never a collection DB."""

import json
import os
import sqlite3
import stat
from pathlib import Path
from urllib.parse import quote


class SnapshotError(ValueError):
    """The supplied file is not an admissible offline snapshot."""


def _checked_path(path):
    path = Path(os.path.abspath(os.fspath(path)))
    protected = [Path.home() / 'Sandbox/grokbot/market-data',
                 Path.home() / 'ZCodeProject/ZmartyChat-paper-grid',
                 Path.home() / 'Sandbox/grokbot/vercel-publisher',
                 Path.home() / 'ZCodeProject/GrokBot',
                 Path.home() / 'Sandbox/grokbot/zmarty-paper-runtime',
                 Path.home() / 'Sandbox/grokbot/trader-v2-runtime',
                 Path.home() / 'Library/LaunchAgents',
                 Path.home() / '.openclaw-secrets', Path.home() / '.openclaw',
                 Path.home() / '.claude', Path.home() / '.paperclip']
    if any(path == root or root in path.parents for root in protected):
        raise SnapshotError('protected data path is forbidden')
    for component in reversed((path, *path.parents)):
        try:
            info = component.lstat()
        except OSError as error:
            raise SnapshotError('offline snapshot file is unavailable') from error
        if stat.S_ISLNK(info.st_mode):
            raise SnapshotError('offline snapshot path contains a symlink')
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise SnapshotError('offline snapshot must be a detached regular file')
    return path


def _manifest(path):
    manifest = _checked_path(path.parent / 'snapshot.json')
    if manifest.stat().st_size > 65536:
        raise SnapshotError('snapshot manifest is too large')
    try:
        document = json.loads(manifest.read_text())
    except (OSError, ValueError) as error:
        raise SnapshotError('invalid offline snapshot manifest') from error
    if not isinstance(document, dict) or document.get('offline_copy') is not True:
        raise SnapshotError('manifest must attest offline_copy')
    if document.get('source') != 'kucoin-public':
        raise SnapshotError('manifest must identify kucoin-public source')
    return document


class Snapshot:
    """No defaults, copying, live access, network calls, or writes.

    The operator must supply a detached, checkpointed copy and snapshot.json.
    A current registry is deliberately unused: historical membership is inferred
    only from ticker records actually observed by the replay timestamp.
    """

    def __init__(self, path):
        self.path = _checked_path(path)
        self.manifest = _manifest(self.path)
        if any(os.path.lexists(str(self.path) + suffix)
               for suffix in ('-wal', '-shm', '-journal')):
            raise SnapshotError('snapshot must be detached from SQLite journals')
        uri = 'file:' + quote(str(self.path), safe='/') + '?mode=ro&immutable=1'
        self.connection = sqlite3.connect(uri, uri=True)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute('PRAGMA query_only=ON')
        try:
            self._validate_schema()
        except Exception:
            self.connection.close()
            raise

    def _validate_schema(self):
        required = {
            'klines': {'symbol', 'interval', 'time_ms', 'open', 'high', 'low',
                       'close', 'volume', 'turnover'},
            'ticker_snapshots': {'symbol', 'time_ms', 'observed_at_ms',
                                 'source_time_ms', 'raw_json', 'turnover_24h',
                                 'funding_rate'},
            'top_of_book': {'symbol', 'time_ms', 'observed_at_ms', 'bid',
                            'ask', 'bid_size', 'ask_size'},
            'funding': {'symbol', 'time_ms', 'rate', 'period_ms'},
        }
        for table, columns in required.items():
            found = {row['name'] for row in self.connection.execute(
                f'PRAGMA table_info({table})')}
            if not columns <= found:
                raise SnapshotError('offline snapshot schema is incomplete')

    def close(self):
        self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def candles(self, pair, start_ms, end_ms):
        """Completed one-minute candles with open times in [start, end)."""
        return [dict(row) for row in self.connection.execute(
            "SELECT * FROM klines WHERE symbol=? AND interval='1m' "
            'AND time_ms>=? AND time_ms+60000<=? ORDER BY time_ms',
            (pair, start_ms, end_ms))]

    def funding(self, pair, start_ms, end_ms):
        """Actual eight-hour settlements in (start, end], never predictions."""
        return [dict(row) for row in self.connection.execute(
            'SELECT * FROM funding WHERE symbol=? AND time_ms>? '
            'AND time_ms<=? AND period_ms=28800000 ORDER BY time_ms',
            (pair, start_ms, end_ms))]

    def bounds(self):
        row = self.connection.execute(
            "SELECT MIN(time_ms), MAX(time_ms)+60000 FROM klines "
            "WHERE interval='1m'").fetchone()
        return tuple(row)

    def market_bounds(self):
        """Observation envelope only; per-symbol staleness/gaps remain mandatory."""
        ranges = [self.connection.execute(
            f'SELECT MIN(observed_at_ms), MAX(observed_at_ms) FROM {table}'
        ).fetchone() for table in ('ticker_snapshots', 'top_of_book')]
        if any(row[0] is None for row in ranges):
            return None, None
        return max(row[0] for row in ranges), max(row[1] for row in ranges)

    def records(self, at_ms, pairs=None):
        """Materialize a bounded requested set; full scanners use iter_records."""
        return list(self.iter_records(at_ms, pairs))

    def iter_records(self, at_ms, pairs=None):
        """Stream one coin's seven-day history at a time, preserving as-of checks."""
        universe = self.symbols(at_ms)
        if pairs is not None:
            requested = set(pairs)
            universe = [pair for pair in universe if pair in requested]
        for pair in universe:
            row = self.market(pair, at_ms)
            market = _scanner_market(row)
            yield dict(pair=pair, market=market,
                       bars=self.candles(pair, at_ms - 604800000, at_ms))

    def symbols(self, at_ms):
        return [row[0] for row in self.connection.execute(
            'SELECT DISTINCT symbol FROM ticker_snapshots '
            'WHERE observed_at_ms<=? AND time_ms<=? '
            'AND (source_time_ms IS NULL OR source_time_ms<=?) ORDER BY symbol',
            (at_ms, at_ms, at_ms))]

    def market(self, pair, at_ms):
        ticker = self.connection.execute(
            'SELECT * FROM ticker_snapshots WHERE symbol=? '
            'AND observed_at_ms<=? AND time_ms<=? '
            'AND (source_time_ms IS NULL OR source_time_ms<=?) '
            'ORDER BY observed_at_ms DESC, time_ms DESC LIMIT 1',
            (pair, at_ms, at_ms, at_ms)).fetchone()
        if ticker is None:
            return None
        result = dict(ticker)
        result['ticker_observed_at_ms'] = result.pop('observed_at_ms')
        result['raw'] = _raw_contract(result.pop('raw_json'))
        result['observed_at_ms'] = result['ticker_observed_at_ms']
        result['funding_period_ms'] = result['raw'].get('fundingRateGranularity')
        book = self.connection.execute(
            'SELECT * FROM top_of_book WHERE symbol=? AND observed_at_ms<=? '
            'AND time_ms<=? ORDER BY observed_at_ms DESC, time_ms DESC LIMIT 1',
            (pair, at_ms, at_ms)).fetchone()
        if book:
            result.update({key: book[key] for key in
                           ('bid', 'ask', 'bid_size', 'ask_size')})
            result['book_observed_at_ms'] = book['observed_at_ms']
            result['book_time_ms'] = book['time_ms']
        return result


def _raw_contract(raw_json):
    try:
        value = json.loads(raw_json)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _scanner_market(row):
    raw = row['raw']
    result = dict(raw)
    times = [row.get(key) for key in ('time_ms', 'ticker_observed_at_ms',
                                     'book_observed_at_ms', 'book_time_ms')]
    if row.get('source_time_ms') is not None:
        times.append(row['source_time_ms'])
    result.update(observed_at_ms=min(times) if all(t is not None for t in times) else None,
                  price=row.get('last'), bid=row.get('bid'), ask=row.get('ask'),
                  bestBidSize=row.get('bid_size'), bestAskSize=row.get('ask_size'),
                  quote_turnover_24h=row.get('turnover_24h'),
                  funding_rate=row.get('funding_rate'))
    period = raw.get('currentFundingRateGranularity', raw.get('fundingRateGranularity'))
    result['funding_interval_hours'] = period / 3600000 if isinstance(period, (int, float)) else None
    known = 'expireDate' in raw and 'isInverse' in raw
    result['perpetual'] = (raw['expireDate'] in (None, 0) and raw['isInverse'] is False) if known else None
    return result
