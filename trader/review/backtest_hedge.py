"""Backtest the T5 hedge trigger against the closed paper bots.

Read-only. Reconstructs each closed bot from its own stored specification,
replays its life from `opened_ms` to `closed_ms` through 1m klines, and at
every 15-minute mark asks a family of candidate rules whether it would have
hedged. Where a rule fires, the bot is hedged at that mark (an offsetting leg
sized to the current net position, so the net is flat) and the replay continues
to the real close time. The reported delta is hedged net minus the same
replay's own unhedged net, so replay error cancels out of every number.

Nothing here is live: no state is written, no order exists, both the autopilot
state file and the market database are opened read-only.

    python3 -m trader.review.backtest_hedge --state ... --database ...
"""
import argparse
import json
import math
import sqlite3
import statistics
import sys
from contextlib import closing
from copy import deepcopy
from pathlib import Path

from trader.papergrid import open_bot, close_bot, step
from trader.autopilot import hedge
from trader.data import liquidation_clusters as clusters

MARK_MS = 15 * 60_000
SPEC_KEYS = ('bot_id', 'symbol', 'direction', 'range_low', 'range_high', 'step_pct',
             'grids', 'notional_usdt', 'leverage', 'funding_pct', 'grid_interval',
             'profit_pct_min', 'profit_pct_max', 'accounting_version', 'contract_lots',
             'contract_multiplier', 'maintain_margin', 'risk_limit',
             'fee_rate_maker', 'fee_rate_taker')
RATIOS = (1.0, 1.5, 2.0)
FRACTIONS = (0.5, 0.75)
DISTANCES = (None, 2.0, 5.0)
DEFAULT_STATE = str(Path.home() / 'Sandbox' / 'grokbot' / 'autopilot' / 'state.json')
DEFAULT_DATABASE = str(Path.home() / 'Sandbox' / 'grokbot' / 'market-data'
                       / 'phase-2-20260911' / 'market.sqlite3')


def net(bot):
    """The wrapper-free net of one engine ledger, as policy.net defines it."""
    return (bot['realized_pnl'] + bot['unrealized_pnl']
            - bot['fees_paid'] - bot['funding_paid'])


def spec_from(book):
    """The bot's own opening specification, recovered from its closed ledger.

    `funding_managed` is forced off so the replay charges funding from the
    stored rate instead of silently dropping it: the live ledger's funding was
    applied by the runtime, which the replay cannot reach.
    """
    spec = {key: book[key] for key in SPEC_KEYS if key in book}
    spec['quantity_per_grid'] = book['contracts_per_line']
    spec['funding_managed'] = False
    return spec


def candles(connection, symbol, start_ms, end_ms):
    """Ordered 1m OHLC inside the bot's life; the engine validates each one."""
    rows = connection.execute(
        'SELECT time_ms, open, high, low, close FROM klines WHERE symbol=? '
        "AND interval='1m' AND time_ms>=? AND time_ms<=? ORDER BY time_ms",
        (symbol, start_ms, end_ms))
    return [dict(ts_ms=at, open=o, high=h, low=low, close=c)
            for at, o, h, low, c in rows]


def _cluster_rows(connection, symbol, cache):
    if symbol not in cache:
        try:
            terms = clusters.contract_for(connection, symbol)
        except ValueError:
            cache[symbol] = ((), None)
            return cache[symbol]
        cache[symbol] = (clusters.read_symbol(connection, symbol, 0, 2 ** 62), terms)
    return cache[symbol]


def cluster_view(connection, symbol, at_ms, price, cache):
    """Point-in-time cluster annotation shaped like an annotated radar row.

    Derives the implied clusters from the open interest observed strictly
    before `at_ms`, so no rule can see the future. Symbols with no usable
    contract terms or no snapshots annotate nothing, which fails the cluster
    gate closed rather than inventing a distance.
    """
    rows, terms = _cluster_rows(connection, symbol, cache)
    if terms is None:
        return None
    key = (symbol, at_ms)
    if key not in cache:
        window = at_ms - clusters.WINDOW_HOURS * clusters.HOUR_MS
        usable = [row for row in rows if window <= row[0] <= at_ms]
        cache[key] = clusters.derive(usable, terms, at_ms)['clusters']
    below = clusters._nearest(cache[key], price, above=False)
    above = clusters._nearest(cache[key], price, above=True)
    return dict(liq_clusters_generated_at_ms=at_ms,
                liq_below_pct=below['distance_pct'] if below else 0.0,
                liq_above_pct=above['distance_pct'] if above else 0.0)


def observe(bot, path, view):
    """Replay once, snapshotting the bot at every 15-minute mark.

    Returns (final bot, marks). Each mark carries the index it sits at so a
    hedged branch can resume from exactly there without replaying twice.
    Marks where the bot cannot carry an offsetting leg are not recorded: an
    already two-book NEUTRAL bot has nothing to hedge, and a flat bot has no
    inventory to offset.
    """
    marks = []
    for index, candle in enumerate(path):
        bot, _ = step(bot, candle)
        at = candle['ts_ms']
        if at % MARK_MS == 0 and hedge.hedgeable(bot):
            marks.append(dict(index=index, ts_ms=at, price=bot['last_price'],
                              bot=deepcopy(bot),
                              clusters=view(bot['symbol'], at, bot['last_price'])))
    return bot, marks


def finish(bot, path, close_price, close_ms, reason):
    """Step the remaining candles and flatten exactly as the desk would have."""
    for candle in path:
        bot, _ = step(bot, candle)
    bot, _ = close_bot(bot, close_price, close_ms, reason)
    return net(bot)


def opening_price(book, path):
    """The stored opening price; pre-v2 ledgers never kept one, so use the tape.

    The first 1m open inside the bot's life is the closest observable stand-in;
    it is clamped into the bot's own range because `open_bot` refuses to seed a
    ladder from a price outside it.
    """
    stored = book.get('opening_price')
    if isinstance(stored, (int, float)) and not isinstance(stored, bool):
        return stored
    price = path[0]['open'] if path else book['lines'][book['empty_line']]
    return min(max(price, book['range_low']), book['range_high'])


def replay(connection, wrapper, view):
    """One closed bot: its unhedged replay net, its marks, and its real result."""
    book = wrapper['engine']
    path = candles(connection, book['symbol'], book['opened_ms'], book['closed_ms'])
    opened = open_bot(spec_from(book), opening_price(book, path), book['opened_ms'])
    final, marks = observe(opened, path, view)
    price = final['last_price']
    baseline = finish(deepcopy(final), (), price, book['closed_ms'], book['reason'])
    return dict(bot_id=book['bot_id'], symbol=book['symbol'],
                direction=book['direction'], reason=book['reason'],
                candles=len(path), marks=marks, path=path, baseline=baseline,
                close_price=price, close_ms=book['closed_ms'],
                actual_net=net(book), actual_grids=book['completed_grids'],
                replay_grids=final['completed_grids'])


def hedged_net(case, mark, cache):
    """Net if the bot had been hedged at `mark`, memoised per mark index."""
    index = mark['index']
    if index not in cache:
        bot, _ = hedge.hedge_leg(mark['bot'], mark['price'], mark['ts_ms'])
        cache[index] = finish(bot, case['path'][index + 1:], case['close_price'],
                              case['close_ms'], case['reason'])
    return cache[index]


def first_trigger(case, ratio, fraction, distance):
    """The earliest mark at which this rule fires, or None."""
    for mark in case['marks']:
        fired, _ = hedge.triggered(mark['bot'], mark['clusters'], mark['ts_ms'],
                                   ratio, fraction, distance)
        if fired:
            return mark
    return None


def rule_deltas(cases, ratio, fraction, distance):
    """Per-bot outcome of one rule: (case, mark, hedged net, delta) where it fired."""
    fired = []
    for case in cases:
        mark = first_trigger(case, ratio, fraction, distance)
        if mark is None:
            continue
        value = hedged_net(case, mark, case['hedged'])
        fired.append((case, mark, value, value - case['baseline']))
    return fired


def sweep(cases):
    """Every rule in the family, scored against the same unhedged replays."""
    rows = []
    for ratio in RATIOS:
        for fraction in FRACTIONS:
            for distance in DISTANCES:
                fired = rule_deltas(cases, ratio, fraction, distance)
                rows.append(score(ratio, fraction, distance,
                                  [item[3] for item in fired]))
    return rows


def score(ratio, fraction, distance, deltas):
    """Summary statistics for one rule; an untriggered rule reports zeros."""
    return dict(ratio=ratio, fraction=fraction, distance=distance,
                triggered=len(deltas),
                total=math.fsum(deltas),
                mean=statistics.fmean(deltas) if deltas else 0.0,
                median=statistics.median(deltas) if deltas else 0.0,
                worst=min(deltas) if deltas else 0.0,
                best=max(deltas) if deltas else 0.0,
                improved=sum(1 for value in deltas if value > 0))


def _rule_label(row):
    distance = 'none' if row['distance'] is None else '%.0f%%' % row['distance']
    return 'K=%.1f F=%.2f D=%-4s' % (row['ratio'], row['fraction'], distance)


def table(rows, cases):
    """The report table, baseline first, exactly as docs/hedge-trigger.md shows."""
    total = math.fsum(case['baseline'] for case in cases)
    lines = ['no-op baseline: %d bots, replayed net %+.2f USDT '
             '(live ledger net %+.2f)'
             % (len(cases), total, math.fsum(c['actual_net'] for c in cases)), '',
             '%-22s %9s %11s %11s %11s %11s %9s'
             % ('rule', 'triggered', 'total d', 'mean d', 'median d', 'worst d', 'improved'),
             '-' * 90]
    for row in sorted(rows, key=lambda item: -item['total']):
        lines.append('%-22s %9d %+11.2f %+11.2f %+11.2f %+11.2f %5d/%-3d'
                     % (_rule_label(row), row['triggered'], row['total'], row['mean'],
                        row['median'], row['worst'], row['improved'], row['triggered']))
    return '\n'.join(lines)


def detail(cases, row):
    """Every bot one rule touched, so the headline total can be audited by hand."""
    fired = rule_deltas(cases, row['ratio'], row['fraction'], row['distance'])
    lines = ['per-bot outcome of %s' % _rule_label(row), '',
             '%-6s %-12s %-9s %-14s %9s %10s %10s %10s %9s %7s'
             % ('bot', 'symbol', 'direction', 'close reason', 'hedge min', 'grid then',
                'inv then', 'unhedged', 'hedged', 'delta'), '-' * 106]
    for case, mark, value, delta in sorted(fired, key=lambda item: item[3]):
        shape = hedge.metrics(mark['bot'])
        lines.append('%-6d %-12s %-9s %-14s %9.0f %+10.2f %+10.2f %+10.2f %+9.2f %+7.2f'
                     % (case['bot_id'], case['symbol'], case['direction'], case['reason'],
                        (mark['ts_ms'] - case['path'][0]['ts_ms']) / 60_000,
                        shape['grid_profit'], shape['inventory_pnl'],
                        case['baseline'], value, delta))
    return '\n'.join(lines), [item[3] for item in fired]


def robustness(deltas):
    """Drop-one sensitivity: one bot should not decide whether the rule works."""
    if not deltas:
        return 'no bot triggered, so there is nothing to be robust about'
    total = math.fsum(deltas)
    return ('total %+.2f USDT; drop the single best bot -> %+.2f; '
            'drop the single worst -> %+.2f; positive on %d of %d bots'
            % (total, total - max(deltas), total - min(deltas),
               sum(1 for value in deltas if value > 0), len(deltas)))


def fidelity(cases):
    """How faithfully the replay reproduced each bot, stated before any verdict."""
    lines = ['%-6s %-12s %-9s %-14s %9s %9s %11s %11s'
             % ('bot', 'symbol', 'direction', 'reason', 'candles', 'grids',
                'replay net', 'live net'), '-' * 90]
    for case in sorted(cases, key=lambda item: item['bot_id']):
        lines.append('%-6d %-12s %-9s %-14s %9d %4d/%-4d %+11.2f %+11.2f'
                     % (case['bot_id'], case['symbol'], case['direction'],
                        case['reason'], case['candles'], case['replay_grids'],
                        case['actual_grids'], case['baseline'], case['actual_net']))
    return '\n'.join(lines)


def run(state_path, database_path):
    """Load, replay and sweep. Returns (cases, rule rows)."""
    state = json.loads(Path(state_path).expanduser().read_text())
    uri = Path(database_path).expanduser().absolute().as_uri() + '?mode=ro'
    cache, cases = {}, []
    with closing(sqlite3.connect(uri, uri=True)) as connection:
        def view(symbol, at_ms, price):
            return cluster_view(connection, symbol, at_ms, price, cache)
        for wrapper in state['closed_bots']:
            case = replay(connection, wrapper, view)
            case['hedged'] = {}
            cases.append(case)
    return cases, sweep(cases)


def _parser():
    parser = argparse.ArgumentParser(description='Backtest the hedge trigger.')
    parser.add_argument('--state', default=DEFAULT_STATE)
    parser.add_argument('--database', default=DEFAULT_DATABASE)
    return parser


def main(argv=None, printer=print):
    options = _parser().parse_args(argv)
    cases, rows = run(options.state, options.database)
    printer(fidelity(cases))
    printer('')
    printer(table(rows, cases))
    best = max(rows, key=lambda row: row['total']) if rows else None
    if not best or not best['triggered']:
        printer('')
        printer('no rule in the family ever triggered')
        return 0
    printer('')
    printer('best rule by total delta: %s -> %+.2f USDT over %d bots'
            % (_rule_label(best), best['total'], best['triggered']))
    body, deltas = detail(cases, best)
    printer('')
    printer(body)
    printer('')
    printer(robustness(deltas))
    return 0


if __name__ == '__main__':
    sys.exit(main())
