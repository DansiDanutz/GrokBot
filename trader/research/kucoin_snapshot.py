"""Read an explicitly attested, detached SQLite copy; never a collection DB."""

import json
import math
from bisect import bisect_left
import os
import sqlite3
import stat
from pathlib import Path
from urllib.parse import quote
from trader.strategies.candle_coverage import validate_history, combine_histories, prepare_validated


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
            'AND time_ms>=? AND time_ms<=? ORDER BY time_ms',
            (pair, start_ms, end_ms-60000))]

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



MINUTE_MS = 60000
DAY_MS = 86400000
WEEK_MS = 7*DAY_MS


def _positive_number(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def _observed_time(row):
    return row.get('timestamp_ms', row.get('time_ms'))


def _consistent_rows(rows):
    observed = {}
    fields = ('open', 'high', 'low', 'close', 'volume', 'turnover')
    for row in rows:
        timestamp = _observed_time(row)
        previous = observed.get(timestamp)
        if previous is not None and any(previous.get(key) != row.get(key) for key in fields):
            raise SnapshotError('Conflicting duplicate candle at '+str(timestamp))
        observed[timestamp] = row
    return list(observed.values())


def _validated_unique(rows):
    validated = validate_history(rows)
    if validated.validated and len(validated) != len(rows):
        return validate_history(validated.rows)
    return validated


def _indicator_history(rows, start, end):
    """Carry only already observed closes; placeholders are never executions."""
    rows = _consistent_rows(rows)
    observed = {int(_observed_time(row)): row for row in rows if start <= _observed_time(row) < end}
    earlier = [row for row in rows if _observed_time(row) < start]
    last = earlier[-1]['close'] if earlier else None
    result = []
    for timestamp in range(start, end, MINUTE_MS):
        row = observed.get(timestamp)
        if row is not None:
            last = row['close']
            result.append(dict(row, timestamp_ms=timestamp, indicator_only=False))
        elif last is not None:
            result.append(dict(timestamp_ms=timestamp, time_ms=timestamp, open=last,
                high=last, low=last, close=last, volume=0, turnover=0,
                indicator_only=True, synthetic=True))
    expected = (end-start)//MINUTE_MS
    return result, dict(observed=len(observed), expected=expected,
                        ratio=len(observed)/expected if expected else 0)


def _quote_turnover(bars, metadata, volume_unit):
    rows = [row for row in bars if not row.get('indicator_only')]
    if rows and all(type(row.get('turnover')) in (int, float) and math.isfinite(row['turnover'])
                    and (row['turnover'] > 0 or row['turnover'] == 0 and row.get('volume') == 0) for row in rows):
        return sum(row['turnover'] for row in rows), 'observed candle quote turnover sum (USDT)'
    multiplier = metadata.get('multiplier')
    if not rows or volume_unit not in ('contracts', 'base', 'quote_usdt'):
        return None, 'candle volume units are unknown'
    if volume_unit == 'contracts' and not _positive_number(multiplier):
        return None, 'contract multiplier is unknown; volume is not quote turnover'
    total = 0.
    for row in rows:
        volume = row.get('volume')
        if type(volume) not in (int, float) or not math.isfinite(volume) or volume < 0:
            return None, 'candle volume is unknown'
        value = volume if volume_unit == 'quote_usdt' else volume*row['close']
        total += value*multiplier if volume_unit == 'contracts' else value
    return total, 'estimated quote turnover from '+volume_unit+' volume and candle close'+(
        '; retrospective contract multiplier' if volume_unit == 'contracts' else '')


class HistoricalSnapshot:
    """Explicit candle-only adapter; keeps current metadata assumptions visible.

    Daily prefetch is an IO cache only: every returned series and membership list
    is sliced at the requested completed-candle time. Indicator gaps are carried
    forward; ``candles`` always returns actual observations only.
    """
    historical_candle_only = True

    def __init__(self, source, parameters=None):
        self.source, self.parameters = source, dict(parameters or {})
        self.manifest = dict(source.manifest, replay_filter_mode='candle-only filters')
        self.connection = source.connection
        self._cache, self._history, self._metadata_cache = {}, {}, {}
        self._prepared_cache, self._record_cache, self._quote_cache = {}, {}, {}
        self._index = {row[0]: (row[1], row[2]+MINUTE_MS) for row in self.connection.execute(
            "SELECT symbol,MIN(time_ms),MAX(time_ms) FROM klines WHERE interval='1m' GROUP BY symbol")}
        self._market_bounds = source.market_bounds()
        self.stats = dict(history_block_reads=0, historical_market_reads=0)
        self.volume_unit = self.parameters.get('candle_volume_unit', source.manifest.get('candle_volume_unit', 'contracts'))
        spread = self.parameters.get('historical_spread_bps', 10)
        if not _positive_number(spread) and spread != 0:
            raise ValueError('historical_spread_bps must be finite and nonnegative')
        self.spread_bps = spread

    def bounds(self):
        return (min((row[0] for row in self._index.values()), default=None),
                max((row[1] for row in self._index.values()), default=None))

    def market_bounds(self):
        return self._market_bounds

    def symbols(self, at_ms):
        return sorted(pair for pair, (first, last) in self._index.items()
                      if first+MINUTE_MS <= at_ms and pair.endswith('USDTM'))

    def _block(self, pair, at_ms):
        start, end = at_ms//DAY_MS*DAY_MS-WEEK_MS-DAY_MS, (at_ms//DAY_MS+1)*DAY_MS
        previous = self._cache.get(pair)
        if previous and previous['start'] <= start and previous['end'] >= end:
            return previous
        retained = [] if previous is None else [row for row in previous['rows']
                    if start <= _observed_time(row) < min(end, previous['end'])]
        read_start = max(start, previous['end']) if previous and previous['start'] <= start else start
        fresh = self.source.candles(pair, read_start, end)
        self.stats['history_block_reads'] += 1
        rows = retained+fresh if read_start > start else fresh
        fresh_validated = _validated_unique(fresh)
        if previous and read_start > start:
            validated = combine_histories([previous['validated'].window(start, read_start), fresh_validated])
        else:
            validated = fresh_validated
        block = dict(start=start, end=end, rows=rows, times=[_observed_time(row) for row in rows], validated=validated)
        self._cache[pair] = block
        return block

    def history(self, pair, at_ms):
        previous = self._history.get(pair)
        if previous and previous[0] == at_ms:
            return previous[1], previous[2]
        block = self._block(pair, at_ms)
        cutoff = bisect_left(block['times'], at_ms)
        beginning = bisect_left(block['times'], at_ms-WEEK_MS)
        seed_index = bisect_left(block['times'], block['times'][beginning-1]) if beginning else 0
        rows = block['rows'][seed_index:cutoff]
        bars, coverage = _indicator_history(rows, at_ms-WEEK_MS, at_ms)
        self._history[pair] = at_ms, bars, coverage
        return bars, coverage

    def prior_seed(self, pair, at_ms):
        block = self._block(pair, at_ms)
        end = bisect_left(block['times'], at_ms-WEEK_MS)
        if not end:
            return None
        start = bisect_left(block['times'], block['times'][end-1])
        return dict(_consistent_rows(block['rows'][start:end])[-1])

    def candles(self, pair, start_ms, end_ms):
        block = self._block(pair, end_ms)
        low, high = bisect_left(block['times'], start_ms), bisect_left(block['times'], end_ms)
        return [dict(row) for row in block['rows'][low:high]]

    def funding(self, pair, start_ms, end_ms):
        return self.source.funding(pair, start_ms, end_ms)

    def metadata(self, pair):
        if pair not in self._metadata_cache:
            row = self.connection.execute('SELECT raw_json, observed_at_ms FROM ticker_snapshots '
                'WHERE symbol=? ORDER BY observed_at_ms DESC LIMIT 1', (pair,)).fetchone()
            raw = _raw_contract(row['raw_json']) if row else {}
            allowed = ('tickSize', 'lotSize', 'multiplier', 'quoteCurrency', 'expireDate', 'isInverse', 'assetClass')
            values = {key: raw.get(key) for key in allowed}
            for key in ('tickSize', 'lotSize', 'multiplier'):
                try:
                    value = float(values[key])
                    values[key] = value if math.isfinite(value) and value > 0 else None
                except (TypeError, ValueError):
                    values[key] = None
            self._metadata_cache[pair] = dict(values,
                metadata_observed_at_ms=row['observed_at_ms'] if row else None,
                metadata_basis='retrospective copied contract specifications')
        return self._metadata_cache[pair]

    def _scanner_market(self, pair, at_ms, bars, coverage, sparse=False, seed=None):
        specs = self.metadata(pair)
        daily = [row for row in bars if row['timestamp_ms'] >= at_ms-DAY_MS]
        turnover, basis = _quote_turnover(daily, specs, self.volume_unit)
        ratios = [coverage['ratio']] + [
            sum(row['timestamp_ms'] >= at_ms-n*MINUTE_MS for row in bars)/n if sparse else
            sum(not row['indicator_only'] for row in bars[-n:])/n for n in (1440, 240)]
        perpetual = specs['expireDate'] in (None, 0) and specs['isInverse'] is False
        asset = specs.get('assetClass')
        result = dict(specs, filter_mode='candle-only filters', active=True, perpetual=perpetual,
            quote_currency=specs.get('quoteCurrency'), asset_class=asset.lower() if isinstance(asset, str) else None,
            membership_basis='observed_candles',
            listed_at_ms=self._index[pair][0], observed_at_ms=at_ms,
            price=bars[-1]['close'] if bars else seed['close'] if seed else None, quote_turnover_24h=turnover,
            turnover_basis=basis, candle_volume_unit=self.volume_unit,
            candle_coverage_ratio=min(ratios), candle_coverage=coverage,
            tick_size=specs.get('tickSize'), lot_size=specs.get('lotSize'),
            funding_rate=0., funding_rate_8h=0., funding_interval_hours=8,
            funding_signal_basis='unknown historical funding sign assumed neutral',
            filter_assumptions=['spread, book depth and funding filters unavailable',
                                'copied contract metadata is retrospective, not historical observation'])
        available = self._causal_quote(pair, at_ms)
        if available is not None:
            result = dict(available[1], filter_mode='quote/book filters',
                turnover_basis='observed 24h quote turnover', candle_volume_unit=self.volume_unit,
                listed_at_ms=available[1].get('listed_at_ms', available[1].get('firstOpenDate')),
                membership_basis='causal_quote_record', candle_quote_turnover_24h=turnover,
                candle_turnover_basis=basis, candle_listed_at_ms=self._index[pair][0],
                candle_coverage_ratio=min(ratios), candle_coverage=coverage,
                metadata_basis='causal copied quote/book contract record')
        return result

    def _causal_quote(self, pair, at_ms):
        previous = self._quote_cache.get(pair)
        if previous is not None and previous[0] == at_ms:
            return previous[1]
        first, last = self._market_bounds
        row = self.source.market(pair, at_ms) if first is not None and first <= at_ms <= last+3600000 else None
        result = None
        if row and row.get('bid') is not None and row.get('ask') is not None:
            context = _scanner_market(row)
            observed = context.get('observed_at_ms')
            if observed is not None and 0 <= at_ms-observed <= 3600000:
                result = row, context
        self._quote_cache[pair] = at_ms, result
        return result

    def _prepared_record(self, pair, at_ms):
        previous = self._record_cache.get(pair)
        if previous is not None and previous[0] == at_ms:
            return previous[1]
        block = self._block(pair, at_ms)
        view = block['validated'].window(at_ms-WEEK_MS, at_ms)
        seed = self.prior_seed(pair, at_ms)
        if not view.validated:
            bars, coverage = self.history(pair, at_ms)
            return dict(pair=pair, bars=bars, prior_seed=seed,
                        market=self._scanner_market(pair, at_ms, bars, coverage))
        prepared = prepare_validated(view, at_ms, prior_seed=seed, previous=self._prepared_cache.get(pair))
        coverage = dict(observed=len(view), expected=10080, ratio=len(view)/10080)
        record = dict(pair=pair, bars=view.rows, prior_seed=seed, prepared=prepared,
                      market=self._scanner_market(pair, at_ms, view.rows, coverage, sparse=True, seed=seed))
        self._prepared_cache[pair] = prepared
        self._record_cache[pair] = at_ms, record
        return record

    def iter_records(self, at_ms, pairs=None):
        wanted = None if pairs is None else set(pairs)
        for pair in self.symbols(at_ms):
            if wanted is not None and pair not in wanted:
                continue
            record = self._prepared_record(pair, at_ms)
            try:
                yield record
            finally:
                self._release_invalid_record(pair, record)

    def _release_invalid_record(self, pair, record):
        """Drop rejected dense histories after consumption, retaining valid reuse.

        Returned records own their references. A suspended older iterator must
        not evict a newer record loaded for the same pair while it was yielded.
        """
        if record.get('prepared', {}).get('valid') is not False:
            return
        current = self._record_cache.get(pair)
        if current is not None and current[1] is not record:
            return
        for cache in (self._cache, self._history, self._prepared_cache,
                      self._record_cache, self._quote_cache):
            cache.pop(pair, None)

    def records(self, at_ms, pairs=None):
        return list(self.iter_records(at_ms, pairs))

    def market(self, pair, at_ms):
        available = self._causal_quote(pair, at_ms)
        if available is not None:
            return available[0]
        block = self._block(pair, at_ms)
        index = bisect_left(block['times'], at_ms)-1
        if index < 0:
            return None
        row, half = block['rows'][index], self.spread_bps/20000
        price = row['close']
        self.stats['historical_market_reads'] += 1
        return dict(bid=price*(1-half), ask=price*(1+half), observed_at_ms=at_ms,
            book_observed_at_ms=at_ms, candle_observed_at_ms=_observed_time(row)+MINUTE_MS,
            assumed=True, assumption='modeled bid/ask around last observed candle close',
            assumed_spread_bps=self.spread_bps, raw=self.metadata(pair))
