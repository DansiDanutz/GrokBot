"""Bounded causal chart/funding enrichment around a supplied offline snapshot.

This adapter opens no database, files, or network connection. The caller supplies
Snapshot/HistoricalSnapshot and already-loaded public funding histories. Warmup
reads 55 closed UTC days plus one earlier day for a causal seed; an absent seed
remains unknown. Only the latest 55 buckets per timeframe and the unfinished UTC
day's actual minutes remain cached. Forward calls read new closed minutes only.

The default 128-symbol LRU is intended for the >=95%-covered candidate subset,
plus BTC/ETH/SOL references. Short-history candidates never trigger long warmup.
Eviction, a backwards asof, or a jump beyond one day requires bounded warmup. Funding cadence is
inferred, never independently verified; schedule changes conservatively suppress
forecast terms until the upstream helper's historical irregularity is resolved.
"""
from bisect import bisect_left
from collections import OrderedDict
from copy import deepcopy
import hashlib
import json

from trader.features.chart_read import read_chart
from trader.features.regime import gate_entry
from trader.features.timeframes import TIMEFRAME_MINUTES, aggregate_candles, _observations
from trader.research.kucoin_snapshot import HistoricalSnapshot
from trader.research.public_funding import latest_funding_terms, _records
from trader.strategies.candle_coverage import is_prepared_history


MINUTE_MS, DAY_MS = 60000, 86400000
WEEK_MS = 7 * DAY_MS
HISTORY_BARS = 55
REFERENCES = ('XBTUSDTM', 'ETHUSDTM', 'SOLUSDTM')


def _at(value):
    if type(value) is not int or value < 0:
        raise ValueError('asof_ms must be a nonnegative integer')
    return value // MINUTE_MS * MINUTE_MS


def _actual_crossing_bars(rows, at_ms):
    start = at_ms - WEEK_MS
    result = []
    for row in rows:
        if row.get('synthetic') or row.get('indicator_only') or row.get('observed') is False:
            continue
        timestamp = row.get('timestamp_ms', row.get('time_ms'))
        if type(timestamp) is not int:
            raise ValueError('Actual crossing candle needs an integer timestamp')
        if start <= timestamp and timestamp + MINUTE_MS <= at_ms:
            result.append(row)
    return result


def _coverage(record, bars, at_ms):
    prepared = record.get('prepared')
    if (is_prepared_history(prepared) and prepared['coverage']['expected'] == 10080
            and prepared['coverage']['window_end_ms'] == _at(at_ms)):
        count = prepared['coverage']['actual']
    else:
        count = len({row.get('timestamp_ms', row.get('time_ms')) for row in bars})
    eligible = count * 100 >= 10080 * 95
    return dict(observed=count, expected=10080, fraction=count/10080, eligible=eligible,
                reason=None if eligible else 'Observed seven-day minute coverage is below 95%; chart warmup skipped')


def _funding_inputs(histories):
    records, provenance = {}, {}
    for pair, value in (histories or {}).items():
        rows = value.get('records') if isinstance(value, dict) else value
        records[pair] = _records(rows, pair, 0, 2**63-1)
        provenance[pair] = {'traversal_complete': value.get('complete') is True
                            if isinstance(value, dict) else False}
    return records, provenance


def _canonical_digest(value):
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')
    except (TypeError, ValueError) as exc:
        raise ValueError('Chart input provenance must be finite JSON-serializable data') from exc
    return hashlib.sha256(encoded).hexdigest()


def _chart_parameters(parameters):
    supported = {'bias_mode', 'minimum_confidence', 'neutral_band'}
    flat = {key: deepcopy(parameters[key]) for key in supported if key in parameters}
    nested = parameters.get('chart', {})
    if not isinstance(nested, dict) or set(nested) - supported:
        raise ValueError('Unsupported chart options')
    for key in set(flat) & set(nested):
        if flat[key] != nested[key]:
            raise ValueError(f'Conflicting flat and nested chart option: {key}')
    return dict(flat, **deepcopy(nested))


def _regime_evidence(report, enabled):
    result = deepcopy(report)
    result['enabled'] = enabled
    result['would_allow'] = list(report['allowed_directions'])
    if not enabled:
        result['allowed_directions'] = ['long', 'short', 'neutral']
        result['disabled_reason'] = 'Regime restriction disabled by registered parameter; original gate evidence retained'
        if result.get('requested_direction') is not None:
            result['requested_allowed'] = True
    return result


def _append_frames(cached, rows, asof_ms, prior_seed, stats):
    times = [row['timestamp_ms'] for row in rows]
    for name, minutes in TIMEFRAME_MINUTES.items():
        duration = minutes * MINUTE_MS
        end = asof_ms // duration * duration
        previous = cached.get(name, [])
        start = previous[-1]['end_ms'] if previous else max(0, end-HISTORY_BARS*duration)
        if start >= end:
            cached.setdefault(name, [])
            continue
        index = bisect_left(times, start)
        seed = rows[index-1] if index else prior_seed
        additions = aggregate_candles(rows[index:], name, asof_ms, start_ms=start,
                                      indicator_fill=True, prior_seed=seed)
        if additions and previous:
            additions[0]['crossing_eligible'] = (additions[0]['observed'] and previous[-1]['observed']
                                                 and previous[-1]['end_ms'] == additions[0]['timestamp_ms'])
        cached[name] = (previous + additions)[-HISTORY_BARS:]
        if cached[name]:
            cached[name][0]['crossing_eligible'] = False
        stats['aggregation_calls'] += 1


class ChartSnapshot:
    """Enrich income_chart_v3 records; delegate other snapshot APIs unchanged.

    Flat parameters accept read_chart's bias_mode, minimum_confidence, and
    neutral_band. Optional ``parameters['chart']`` adds options; conflicts reject.
    ``regime_gate=False`` retains evidence but removes the direction restriction.
    ``funding_histories`` maps symbols to canonical record lists
    or public_funding checkpoint dictionaries. Cash events receive no forecast lag.
    """
    income_chart_v3 = True

    def __init__(self, source, parameters=None, *, funding_histories=None,
                 membership=None, cache_symbols=128):
        if type(cache_symbols) is not int or cache_symbols < 1:
            raise ValueError('cache_symbols must be a positive integer')
        self.source = source
        self.parameters = deepcopy(dict(parameters or {}))
        self.parameters.setdefault('strategy', 'income_chart_v3')
        self.manifest = dict(getattr(source, 'manifest', {}), chart_strategy='income_chart_v3',
                             chart_history_bars=HISTORY_BARS)
        self._reader = source.source if isinstance(source, HistoricalSnapshot) else source
        self._funding, self._funding_provenance = _funding_inputs(funding_histories)
        self.membership = deepcopy(membership)
        self.manifest.update(chart_membership_sha256=_canonical_digest(self.membership),
            chart_funding_sha256=_canonical_digest({'records': self._funding,
                                                   'traversal_provenance': self._funding_provenance}))
        self._capacity = cache_symbols
        self._cache = OrderedDict()
        self._references = None
        self._chart_options = _chart_parameters(self.parameters)
        self._regime_enabled = self.parameters.get('regime_gate', True)
        if type(self._regime_enabled) is not bool:
            raise ValueError('regime_gate must be boolean')
        self.stats = dict(warmup_reads=0, incremental_reads=0, aggregation_calls=0, cache_evictions=0)

    def __getattr__(self, name):
        return getattr(self.source, name)

    def for_parameters(self, parameters):
        """Reuse equal normalized options; changed variants get independent caches.

        All options participate in equality, so a setup-only change also produces
        a fresh wrapper. The supplied source, actual funding records, traversal
        provenance, membership and capacity remain available to every variant.
        """
        normalized = deepcopy(dict(parameters or {}))
        normalized.setdefault('strategy', 'income_chart_v3')
        if normalized == self.parameters:
            return self
        histories = {pair: dict(records=rows, complete=self._funding_provenance[pair]['traversal_complete'])
                     for pair, rows in self._funding.items()}
        return ChartSnapshot(self.source, normalized, funding_histories=histories,
                             membership=self.membership, cache_symbols=self._capacity)

    def _load(self, pair, asof_ms):
        end = _at(asof_ms)
        cached = self._cache.get(pair)
        reuse = (cached is not None and cached['asof_ms'] <= asof_ms
                 and end-cached['end_ms'] <= DAY_MS)
        if reuse and cached['end_ms'] == end:
            cached['asof_ms'] = asof_ms
            self._cache.move_to_end(pair)
            return cached
        warmup_start = max(0, end//DAY_MS*DAY_MS-(HISTORY_BARS+1)*DAY_MS)
        start = cached['end_ms'] if reuse else warmup_start
        fresh = self._reader.candles(pair, start, end)
        self.stats['incremental_reads' if reuse else 'warmup_reads'] += 1
        observations = _observations((cached['minutes'] if reuse else []) + fresh, asof_ms)
        rows = [observations[time] for time in sorted(observations)]
        frames = deepcopy(cached['frames']) if reuse else {}
        old_seed = cached['seed'] if reuse else None
        _append_frames(frames, rows, asof_ms, old_seed, self.stats)
        day_start = end//DAY_MS*DAY_MS
        older = [row for row in rows if row['timestamp_ms'] < day_start]
        seed = older[-1] if older else old_seed
        cached = dict(asof_ms=asof_ms, end_ms=end, frames=frames, seed=seed,
                      minutes=[row for row in rows if row['timestamp_ms'] >= day_start])
        self._cache[pair] = cached
        self._cache.move_to_end(pair)
        if len(self._cache) > self._capacity:
            self._cache.popitem(last=False)
            self.stats['cache_evictions'] += 1
        return cached

    def frames(self, pair, asof_ms):
        """Return independent lists of the latest 55 closed bars of every timeframe."""
        return deepcopy(self._load(pair, asof_ms)['frames'])

    def cache_info(self):
        return dict(self.stats, cached_symbols=len(self._cache), capacity=self._capacity,
                    cached_minute_rows=sum(len(row['minutes']) for row in self._cache.values()))

    def chart_reads(self, asof_ms):
        _at(asof_ms)
        if self._references is None or self._references[0] != asof_ms:
            reads = {pair: read_chart(self.frames(pair, asof_ms), asof_ms, **self._chart_options)
                     for pair in REFERENCES}
            self._references = asof_ms, reads
        return deepcopy(self._references[1])

    def _terms(self, pair, asof_ms):
        diagnostics = latest_funding_terms(self._funding.get(pair, []), asof_ms)
        terms = None
        if diagnostics['usable_for_projection']:
            terms = dict(rate=diagnostics['funding_rate'], interval_ms=diagnostics['funding_interval_ms'],
                         observed_at_ms=diagnostics['available_at_ms'])
        return terms, diagnostics

    def iter_records(self, at_ms, pairs=None):
        _at(at_ms)
        reader = getattr(self.source, 'iter_records', None)
        reader = reader if callable(reader) else self.source.records
        for source_record in reader(at_ms, pairs):
            pair = source_record['pair']
            bars = _actual_crossing_bars(source_record['bars'], at_ms)
            coverage = _coverage(source_record, bars, at_ms)
            terms, diagnostics = self._terms(pair, at_ms)
            frames, regime = {name: [] for name in TIMEFRAME_MINUTES}, None
            if coverage['eligible']:
                frames = self.frames(pair, at_ms)
                regime = gate_entry(pair, self.chart_reads(at_ms), at_ms, membership=self.membership,
                                     minimum_confidence=self._chart_options.get('minimum_confidence', .75))
                regime = _regime_evidence(regime, self._regime_enabled)
            yield dict(source_record, bars=bars, frames=frames, funding_terms=terms,
                       funding_diagnostics=diagnostics, regime=regime, chart_data_status=coverage)

    def records(self, at_ms, pairs=None):
        return list(self.iter_records(at_ms, pairs))

    def funding(self, pair, start_ms, end_ms):
        """Supplied actual settlement records in (start, end], with no publication lag."""
        _at(start_ms)
        _at(end_ms)
        if end_ms < start_ms:
            raise ValueError('Funding end must not precede start')
        return [dict(row) for row in self._funding.get(pair, []) if start_ms < row['timestamp_ms'] <= end_ms]

    def funding_coverage(self, pair, start_ms, end_ms):
        """Infer due-time coverage from past cadence; never inspect future brackets."""
        events = self.funding(pair, start_ms, end_ms)
        terms = latest_funding_terms(self._funding.get(pair, []), start_ms)
        interval = terms['funding_interval_ms']
        projected = bool(terms['usable_for_projection'] and interval)
        due, unexpected = [], []
        if projected:
            first = terms['settlement_timestamp_ms'] + interval
            first += max(0, (start_ms-first)//interval+1)*interval
            due = list(range(first, end_ms+1, interval))
            expected = set(due)
            unexpected = [row['timestamp_ms'] for row in events if row['timestamp_ms'] not in expected]
        observed = {row['timestamp_ms'] for row in events}
        missing = [timestamp for timestamp in due if timestamp not in observed]
        complete = projected and not missing and not unexpected
        return dict(pair=pair, start_ms=start_ms, end_ms=end_ms, known=False, verified=False,
                    records_available=len(events), modeled_history_available=projected,
                    modeled_complete=complete, expected_interval_ms=interval,
                    missing_expected_settlements=len(missing) if projected else None,
                    missing_timestamps_ms=missing, irregular_timestamps_ms=unexpected,
                    basis='inferred regular past cadence; no future bracket or independent completeness proof',
                    historical_terms_verified=False,
                    **self._funding_provenance.get(pair, {'traversal_complete': False}))
