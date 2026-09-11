"""Offline, deterministic paper replay from an explicit checkpoint and saved quotes."""
import argparse
from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from paper_grid import engine, coinglass

MAX_BYTES = 32 * 1024 * 1024
MAX_TICKS = 25000
MAX_EVENTS = 100000
SYMBOL = re.compile(r'[\w.-]{1,40}\Z', re.UNICODE)
STATE_KEYS = ('mode', 'cash', 'positions', 'realized_pnl', 'unpaid_liabilities',
              'day', 'day_start_equity', 'halted_day', 'last_run', 'last_rotation',
              'symbol_closed_at', 'last_equity')
POSITION_KEYS = ('contracts', 'quantity', 'cost_basis', 'entry_fees', 'funding_accrued',
                 'funding_time', 'opened_at', 'adds', 'multiplier', 'lot_size',
                 'last_fill', 'last_fill_at', 'last_bid', 'last_mark', 'score',
                 'funding_rate', 'funding_interval_hours', 'average_price')
LIMITATIONS = [
    'Replay uses saved shortlist and held-position quotes, not the full historical universe.',
    'Changed strategies can hold symbols absent from later observations; missing coverage is flagged.',
    'Fills and stops occur only at recorded ticks with displayed full-order depth; no intratick or queue model.',
    'No candle-minimum hindsight fills, optimized parameters, or proof of profitability.',
    'Checkpoint provenance must be independently verified; a current account is not a historical initial state.',
]


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _hash(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _time(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError('invalid replay timestamp')
    return value


def _safe_path(path):
    path = Path(path).absolute()
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('symlink paths are not allowed')
    return path


def _load(path, budget=None):
    path = _safe_path(path)
    if not path.is_file() or path.stat().st_size > MAX_BYTES:
        raise ValueError('input must be a regular JSON file of at most 32 MiB')
    with path.open('rb') as handle:
        raw = handle.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError('input exceeds 32 MiB')
    if budget is not None:
        budget[0] -= len(raw)
        if budget[0] < 0:
            raise ValueError('combined inputs exceed 32 MiB')
    def invalid(_):
        raise ValueError('non-finite JSON values are forbidden')
    return json.loads(raw, parse_constant=invalid)


def _state(state):
    result = {key: deepcopy(state[key]) for key in STATE_KEYS if key in state}
    result['positions'] = {symbol: {key: deepcopy(p[key]) for key in POSITION_KEYS if key in p}
                           for symbol, p in state['positions'].items()}
    return result


def _fixture(doc, overrides=None):
    if (not isinstance(doc, dict) or type(doc.get('schema')) is not int
            or doc['schema'] != 1 or doc.get('mode') != 'paper'):
        raise ValueError('schema 1 paper fixture required')
    if len(_json(doc).encode()) > MAX_BYTES:
        raise ValueError('fixture exceeds 32 MiB')
    start = _time(doc.get('start_at'))
    if not isinstance(doc.get('config'), dict) or not isinstance(doc.get('initial_state'), dict):
        raise ValueError('explicit config and initial_state checkpoint required')
    if set(doc['config']) != set(engine.default_config()):
        raise ValueError('checkpoint must provide the complete engine configuration')
    if overrides is not None and (not isinstance(overrides, dict)
                                 or not set(overrides) <= set(engine.default_config())):
        raise ValueError('unknown configuration overrides')
    config = engine._config(dict(doc['config'], **(overrides or {})))
    state = deepcopy(doc['initial_state'])
    engine._validate_state(state, config)
    state = _state(state)
    for symbol in (*state['positions'], *state['symbol_closed_at']):
        if not isinstance(symbol, str) or not SYMBOL.fullmatch(symbol):
            raise ValueError('invalid checkpoint symbol')
    times = [state.get('last_run'), state.get('last_rotation'), *state['symbol_closed_at'].values()]
    for p in state['positions'].values():
        times.extend(p[k] for k in ('opened_at', 'last_fill_at', 'funding_time'))
    if any(value is not None and _time(value) > start for value in times):
        raise ValueError('checkpoint contains state newer than start_at')
    rows = doc.get('observations')
    if not isinstance(rows, list) or len(rows) > MAX_TICKS:
        raise ValueError('observations list required, at most 25000 records')
    seen, unique = {}, []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('invalid observation')
        at = _time(row.get('time'))
        if at < start or (state.get('last_run') is not None and at <= state['last_run']):
            raise ValueError('observations must follow the explicit checkpoint')
        if not isinstance(row.get('market'), dict) or not isinstance(row.get('coinglass', {}), dict):
            raise ValueError('observation market/features must be objects')
        if len(row['market']) > 100:
            raise ValueError('observation symbol limit exceeded')
        for symbol, quote in row['market'].items():
            if not SYMBOL.fullmatch(symbol) or not isinstance(quote, dict):
                raise ValueError('invalid market symbol or quote')
        key = _json(row)
        if at in seen:
            if seen[at] != key:
                raise ValueError('conflicting observations at the same timestamp')
        else:
            seen[at] = key
            unique.append(deepcopy(row))
    return start, config, state, sorted(unique, key=lambda row: row['time'])


def replay_fixture(doc, overrides=None):
    """Return deterministic evidence; never mutate inputs, fetch data or write files."""
    start, config, checkpoint, rows = _fixture(doc, overrides)
    code = {name: hashlib.sha256((Path(__file__).parent / name).read_bytes()).hexdigest()
            for name in ('engine.py', 'coinglass.py', 'replay.py')}
    provenance = dict(schema=1, checkpoint_hash=_hash(checkpoint), input_hash=_hash(doc),
                      config_hash=_hash(config), config_overrides=deepcopy(overrides or {}),
                      code_hashes=code, checkpoint_at=start)
    result = dict(schema=1, mode='paper', replay_id=_hash(provenance), provenance=provenance,
                  config=config, observations=len(rows), skipped=sum(bool(r.get('skipped')) for r in rows),
                  accounts={}, limitations=LIMITATIONS[:])
    total_events = 0
    for arm in ('baseline', 'liquidation_filter'):
        state = deepcopy(checkpoint)
        ticks, events = [], []
        starting = engine.status(state, {}, start, config)
        peak, drawdown = starting['equity'], 0.0
        last_market, last_at = {}, start
        for row in rows:
            if row.get('skipped'):
                continue
            at, market = row['time'], deepcopy(row['market'])
            if arm == 'liquidation_filter':
                market = coinglass.apply_filter(market, row.get('coinglass', {}), at)
            state, generated = engine.step(state, market, at, config)
            total_events += len(generated)
            if total_events > MAX_EVENTS:
                raise ValueError('replay event limit exceeded')
            events.extend(generated)
            snapshot = engine.status(state, market, at, config)
            peak = max(peak, snapshot['equity'])
            drawdown = max(drawdown, peak - snapshot['equity'])
            ticks.append(dict(time=at, equity=snapshot['equity'], cash=snapshot['cash'],
                              realized_pnl=snapshot['realized_pnl'],
                              equity_is_estimate=snapshot['equity_is_estimate'],
                              unpriced_positions=snapshot['unpriced_positions'],
                              insufficient_exit_depth=snapshot['insufficient_exit_depth'],
                              future_quote_symbols=sorted(k for k, r in market.items()
                                  if type(r.get('quote_time')) in (int, float) and r['quote_time'] > at)))
            last_market, last_at = market, at
        ending = engine.status(state, last_market, last_at, config)
        closes = [e for e in events if e['type'] == 'close']
        fees = sum(e.get('fee', 0) + e.get('exit_fee', 0) for e in events)
        funding = (sum(e.get('funding_model_cost', 0) for e in closes)
                   + sum(p['funding_accrued'] for p in state['positions'].values())
                   - sum(p['funding_accrued'] for p in checkpoint['positions'].values()))
        result['accounts'][arm] = dict(
            ticks=len(ticks), entries=sum(e['type'] == 'open' for e in events),
            adds=sum(e['type'] == 'add' for e in events), trades=len(closes),
            wins=sum(e['net_pnl'] > 0 for e in closes), losses=sum(e['net_pnl'] < 0 for e in closes),
            buy_rejections=sum(e['type'] == 'buy_rejected' for e in events),
            buy_rejection_reasons={reason: sum(e['type'] == 'buy_rejected' and e['reason'] == reason
                                               for e in events)
                                   for reason in sorted({e['reason'] for e in events
                                                        if e['type'] == 'buy_rejected'})},
            starting_equity=starting['equity'], ending_equity=ending['equity'],
            checkpoint_equity_is_estimate=starting['equity_is_estimate'],
            net_equity_change=ending['equity'] - starting['equity'],
            realized_pnl_change=state['realized_pnl'] - checkpoint['realized_pnl'],
            execution_fees=fees, funding_model_cost=funding, sampled_max_drawdown=drawdown,
            coverage_incomplete=any(t['unpriced_positions'] for t in ticks),
            close_reasons={reason: sum(e['reason'] == reason for e in closes)
                           for reason in sorted({e['reason'] for e in closes})},
            equity_history=ticks, events=events, final_state=_state(state))
    if len(_json(result).encode()) > MAX_BYTES:
        raise ValueError('replay output exceeds 32 MiB; split into explicit checkpoints')
    return result


def export_fixture(runtime, checkpoint_path):
    """Read recorded observations and explicit historical state, without writing runtime."""
    runtime = _safe_path(runtime)
    budget = [MAX_BYTES]
    checkpoint = _load(checkpoint_path, budget)
    if 'observations' in checkpoint:
        raise ValueError('checkpoint must not supply observations')
    # Validate before looking at runtime; never derive state from current accounts.
    start, _, state, _ = _fixture(dict(checkpoint, observations=[]))
    live = _load(runtime / 'experiment.json', budget)
    if live.get('mode') != 'paper':
        raise ValueError('paper runtime required')
    rows = list(live.get('observations', []))
    files = live.get('archive_files', [])
    if not isinstance(files, list) or len(files) > 3650:
        raise ValueError('invalid archive manifest')
    for name in sorted(set(files)):
        if not isinstance(name, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}\.json', name):
            raise ValueError('invalid archive filename')
        archive = _load(runtime / 'experiment-archive' / name, budget)
        if archive.get('schema') != 1 or archive.get('day') != name[:-5]:
            raise ValueError('archive schema/day mismatch')
        rows.extend(archive['observations'])
        if len(rows) > MAX_TICKS or len(_json(rows).encode()) > MAX_BYTES:
            raise ValueError('recorded observation limit exceeded')
    selected = [r for r in rows if _time(r.get('time')) >= start
                and (state.get('last_run') is None or r['time'] > state['last_run'])]
    fixture = dict(checkpoint, observations=selected)
    _fixture(fixture)
    return fixture


def write_output(path, value, *, inputs=(), runtime=None):
    """Create a new result file; never overwrite sources, source directories or runtime."""
    path = _safe_path(path)
    root = Path(__file__).resolve().parent.parent
    protected = [root, *(_safe_path(p) for p in inputs)]
    if runtime is not None:
        protected.append(_safe_path(runtime))
    if any(path == p or path.is_relative_to(p) or p.is_relative_to(path) for p in protected):
        raise ValueError('output overlaps protected source, input or runtime')
    raw = _json(value).encode() + b'\n'
    if len(raw) > MAX_BYTES:
        raise ValueError('output exceeds 32 MiB')
    # O_EXCL preserves existing files; callers choose a new explicit output filename.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as handle:
        handle.write(raw)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--config-overrides', type=Path)
    parser.add_argument('--runtime', type=Path)
    parser.add_argument('--checkpoint', type=Path)
    parser.add_argument('--export-input', type=Path)
    args = parser.parse_args(argv)
    try:
        if args.runtime or args.checkpoint or args.export_input:
            if not all((args.runtime, args.checkpoint, args.export_input)) or any(
                    (args.input, args.output, args.config_overrides)):
                raise ValueError('export requires only --runtime, --checkpoint and --export-input')
            fixture = export_fixture(args.runtime, args.checkpoint)
            write_output(args.export_input, fixture, inputs=[args.checkpoint], runtime=args.runtime)
            print(_json(dict(exported=True, observations=len(fixture['observations']))))
            return 0
        if not args.input:
            raise ValueError('--input or explicit checkpoint export required')
        overrides = _load(args.config_overrides) if args.config_overrides else None
        result = replay_fixture(_load(args.input), overrides)
        if args.output:
            write_output(args.output, result, inputs=[p for p in (args.input, args.config_overrides) if p])
        # Summary deliberately omits states, quotes, source paths and event contents.
        print(_json(dict(replay_id=result['replay_id'], observations=result['observations'],
                         skipped=result['skipped'], accounts={arm: {k: v for k, v in data.items()
                           if k not in ('equity_history', 'events', 'final_state')}
                           for arm, data in result['accounts'].items()})))
        return 0
    except (ValueError, OSError, TypeError, KeyError, RecursionError):
        print('Replay rejected invalid input or unsafe/unavailable output; no runtime changes made.', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
