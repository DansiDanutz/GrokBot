"""Backtest the opportunity-cost gate (T6) against the live paper ledger.

Read-only by construction: the state file and the event files are opened for
reading, the market database with an immutable read-only URI, and nothing is
ever written back. For every non-risk close already on the books it answers two
questions:

  1. Did a replacement actually exist? -- was an OPEN recorded in the same or
     the next decision pass, which is the evidence the freed slot was wanted.
  2. What did the close cost? -- the bot is replayed from its own opening
     parameters on 1m klines, past the close, to a mark six hours later.

Three LABEL_FLIP closes is anecdote, not evidence. The table says what happened;
it does not establish that the gate is profitable.

Usage:
    python3 -m trader.review.backtest_opportunity [--hours 6]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from copy import deepcopy
from pathlib import Path

from trader.autopilot import policy
from trader.autopilot.constants import DECISION_INTERVAL_S

HOUR_MS = 3_600_000
MINUTE_MS = 60_000
PASS_MS = DECISION_INTERVAL_S * 1000
NON_RISK_REASONS = ('LABEL_FLIP', 'DROPPED', 'MAX_AGE')
LIVE_ROOT = Path.home() / 'Sandbox' / 'grokbot'
DEFAULT_STATE = LIVE_ROOT / 'autopilot' / 'state.json'
DEFAULT_EVENTS_DIR = LIVE_ROOT / 'autopilot'
DEFAULT_MARKET_DB = LIVE_ROOT / 'market-data' / 'phase-2-20260911' / 'market.sqlite3'
# open_bot copies the specification from these keys; lists and dicts are engine
# runtime, not specification, and are rebuilt from the opening price.
SPEC_KEYS = ('bot_id', 'symbol', 'direction', 'range_low', 'range_high', 'step_pct',
             'grids', 'notional_usdt', 'leverage', 'funding_pct', 'grid_interval',
             'profit_pct_min', 'profit_pct_max', 'accounting_version', 'contract_lots',
             'contract_multiplier', 'maintain_margin', 'risk_limit', 'funding_managed',
             'quantity_is_observed', 'fee_rate_maker', 'fee_rate_taker')


def load_state(path):
    with open(path, 'r', encoding='utf-8') as handle:
        return json.load(handle)


def load_events(directory):
    events = []
    for path in sorted(Path(directory).glob('events*.jsonl')):
        with open(path, 'r', encoding='utf-8') as handle:
            for line in handle:
                line = line.strip()
                if line:
                    events.append(json.loads(line))
    return sorted(events, key=lambda event: event['ts_ms'])


def replacements(events, close_ms, window_ms=PASS_MS):
    """OPEN events in the same decision pass as the close, or the next one."""
    return [event for event in events if event['type'] == 'OPEN'
            and close_ms <= event['ts_ms'] <= close_ms + window_ms]


def candles(db_path, symbol, start_ms, end_ms):
    uri = f'file:{db_path}?mode=ro&immutable=1'
    connection = sqlite3.connect(uri, uri=True)
    try:
        rows = connection.execute(
            'SELECT time_ms, open, high, low, close FROM klines '
            "WHERE symbol = ? AND interval = '1m' AND time_ms BETWEEN ? AND ? "
            'ORDER BY time_ms', (symbol, start_ms, end_ms)).fetchall()
    finally:
        connection.close()
    return [dict(ts_ms=row[0], open=row[1], high=row[2], low=row[3], close=row[4])
            for row in rows]


def _spec(engine):
    spec = {key: engine[key] for key in SPEC_KEYS if key in engine}
    spec['quantity_per_grid'] = engine['contracts_per_line']
    return spec


def replay(wrapper, rows, until_ms):
    """Step the bot's own parameters over 1m candles; returns marks over time.

    The daemon refreshes risk metadata every live pass and a replay has no such
    feed, so the metadata stamp is advanced with the clock. Everything else --
    fills, fees, funding, reserve top-ups, range breaks -- is the paper engine.
    """
    engine = wrapper['engine']
    from trader.papergrid import open_bot
    opening = engine.get('opening_price') or rows[0]['open']
    fresh = open_bot(_spec(engine), opening, engine['opened_ms'])
    state = dict(policy.new_state(engine['opened_ms']),
                 open_bots=[dict(engine=fresh, reserve_usdt=wrapper['reserve_usdt'],
                                 source_section=wrapper['source_section'],
                                 slot_direction=wrapper['slot_direction'], signals=[])])
    marks = []
    for row in rows:
        if row['ts_ms'] > until_ms:
            break
        if state['open_bots']:
            state['open_bots'][0]['engine']['risk_metadata_at_ms'] = row['ts_ms']
        state, _ = policy.advance(state, {engine['symbol']: row})
        current = (state['open_bots'] + state['closed_bots'])[0]['engine']
        marks.append((row['ts_ms'], policy.net(current), current['completed_grids']))
    ended = state['closed_bots'][0]['engine']['reason'] if state['closed_bots'] else None
    return marks, ended


def _mark_at(marks, ts_ms):
    """The last (net, completed grids) mark at or before ts_ms."""
    prior = [mark for mark in marks if mark[0] <= ts_ms]
    return prior[-1][1:] if prior else (None, None)


def evaluate(wrapper, events, db_path, hours):
    engine = wrapper['engine']
    close_ms = engine['closed_ms']
    horizon = close_ms + hours * HOUR_MS
    rows = candles(db_path, engine['symbol'], engine['opened_ms'], horizon)
    result = dict(bot_id=engine['bot_id'], symbol=engine['symbol'],
                  reason=engine['reason'], direction=engine['direction'],
                  slot=wrapper['slot_direction'], grids=engine['completed_grids'],
                  held_h=(close_ms - engine['opened_ms']) / HOUR_MS,
                  net_at_close=policy.net(engine), candles=len(rows),
                  replacements=[event['symbol'] for event in replacements(events, close_ms)])
    if not rows:
        return dict(result, replay_error='no 1m klines')
    try:
        marks, ended = replay(wrapper, rows, horizon)
    except (KeyError, TypeError, ValueError, ZeroDivisionError) as error:
        return dict(result, replay_error=type(error).__name__)
    at_close, grids_at_close = _mark_at(marks, close_ms)
    return dict(result, replay_grids=grids_at_close,
                replay_grids_at_horizon=marks[-1][2] if marks else None,
                replay_at_close=at_close,
                replay_at_horizon=marks[-1][1] if marks else None,
                replay_end_ms=marks[-1][0] if marks else None,
                replay_reason=ended)


def _cell(value, width, digits=2):
    if value is None:
        text = '-'
    elif isinstance(value, float):
        text = f'{value:,.{digits}f}'
    else:
        text = str(value)
    return text.rjust(width)


def table(results, hours):
    header = ('bot  symbol       reason      slot     held_h  grids  net@close  '
              f'replay@close  replay@+{hours}h  delta  replacement')
    lines = [header, '-' * len(header)]
    for item in results:
        replacement = ', '.join(item['replacements']) or 'none'
        at_close, at_horizon = item.get('replay_at_close'), item.get('replay_at_horizon')
        delta = None if None in (at_close, at_horizon) else at_horizon - at_close
        lines.append(' '.join((
            _cell(item['bot_id'], 3), item['symbol'].ljust(12),
            item['reason'].ljust(11), item['slot'].ljust(7),
            _cell(item['held_h'], 6, 1),
            f"{item['grids']}/{item.get('replay_grids', '-')}".rjust(6),
            _cell(item['net_at_close'], 10), _cell(at_close, 13),
            _cell(at_horizon, 12), _cell(delta, 6, 1),
            replacement + (f" [{item['replay_error']}]" if item.get('replay_error') else '')
            + (f" [ended {item['replay_reason']}]" if item.get('replay_reason') else ''))))
    return '\n'.join(lines)


def verdict(results, hours):
    flips = [item for item in results if item['reason'] == 'LABEL_FLIP']
    held = [item for item in flips if not item['replacements']]
    deltas = [item['replay_at_horizon'] - item['replay_at_close'] for item in held
              if item.get('replay_at_close') is not None
              and item.get('replay_at_horizon') is not None]
    lines = [
        f'Non-risk closes examined: {len(results)} '
        f'({len(flips)} LABEL_FLIP, {len(results) - len(flips)} other).',
        f'Would have been HELD by the gate (no replacement OPEN in the same or '
        f'next decision pass): {len(held)} of {len(flips)} LABEL_FLIP closes.',
    ]
    for item in held:
        at_close = item.get('replay_at_close')
        at_horizon = item.get('replay_at_horizon')
        if at_close is None or at_horizon is None:
            lines.append(f"  bot {item['bot_id']} {item['symbol']}: replay unavailable "
                         f"({item.get('replay_error', 'no marks')}).")
            continue
        lines.append(f"  bot {item['bot_id']} {item['symbol']}: net {at_close:,.2f} at close "
                     f'-> {at_horizon:,.2f} at +{hours}h '
                     f'({at_horizon - at_close:+,.2f}'
                     + (f", replay ended {item['replay_reason']}" if item.get('replay_reason') else '')
                     + ').')
    if deltas:
        lines.append(f'Sum of would-have-been change at +{hours}h: {sum(deltas):+,.2f} USDT '
                     f'across {len(deltas)} bot(s).')
    lines.append('Sample size is three. This is an anecdote, not evidence that the '
                 'gate is profitable.')
    return '\n'.join(lines)


def run(state_path, events_dir, db_path, hours):
    state = load_state(state_path)
    events = load_events(events_dir)
    closed = [deepcopy(wrapper) for wrapper in state['closed_bots']
              if wrapper['engine']['reason'] in NON_RISK_REASONS]
    closed.sort(key=lambda wrapper: wrapper['engine']['closed_ms'])
    results = [evaluate(wrapper, events, db_path, hours) for wrapper in closed]
    return results, '\n\n'.join((table(results, hours), verdict(results, hours)))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--state', default=str(DEFAULT_STATE))
    parser.add_argument('--events-dir', default=str(DEFAULT_EVENTS_DIR))
    parser.add_argument('--market-db', default=str(DEFAULT_MARKET_DB))
    parser.add_argument('--hours', type=float, default=6)
    args = parser.parse_args(argv)
    _, text = run(args.state, args.events_dir, args.market_db, args.hours)
    print(text)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
