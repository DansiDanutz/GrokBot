"""Offsetting hedge leg for a directional grid bot carrying adverse inventory.

Evidence (23 closed paper bots): the grid ledger earned +593.13 USDT while the
directional inventory those grids accumulate cost -760.62. Eleven of the 23
exited on RANGE_BREAK, which is exactly the moment a grid bot holds its most
adverse inventory. This module answers that with a hedge, never a close: the
bot gains a second book holding the exact negative of its current net position,
so the inventory loss is frozen at the mark while the original ladder keeps
harvesting grids. The existing `hedge_books` machinery in papergrid.neutral
then owns accounting (`_sync`), funding (`_fund` per book), the liquidation
guard (`risk.protection_needed` already iterates `hedge_books`) and the flatten
(`close_neutral`), all unchanged.

The offsetting book deliberately carries no ladder of its own. A mirror grid
ladder does not hedge: with the grid book's position q*(grids - e(p)) and a
mirrored opposite ladder seeded flat at e0, the combined delta works out to
2q*(e0 - e(p)) - it doubles the exposure to every further adverse line instead
of removing it. A static leg leaves the net delta at P(p) - P(p_hedge), which
is what "net is flat at the mark" means.

Un-hedging is out of scope. Once hedged the bot keeps counting grids (summed
over both books by `_sync`) and leaves through the normal close path.

Pure: every function returns new objects and never mutates its inputs.
"""
import math
from copy import deepcopy

from trader.papergrid import engine
from trader.papergrid.neutral import _sync
from trader.autopilot.constants import (
    HEDGE_ENABLED, HEDGE_INVENTORY_RATIO, HEDGE_POSITION_FRACTION,
    HEDGE_CLUSTER_DISTANCE_PCT)
from trader.radar.liquidity_levels import MAX_AGE_MS as CLUSTER_MAX_AGE_MS

HEDGE_MODEL = 'directional_hedge_v1'
# A long inventory is hurt by prices below it, a short inventory by prices above.
LOSING_SIDE_FIELD = {True: 'liq_below_pct', False: 'liq_above_pct'}


def _finite(value):
    """Real finite numbers only; booleans, strings and NaN are not numbers."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def metrics(bot):
    """Grid ledger, directional inventory cost, and how full the ladder is.

    `inventory_pnl` uses the owner's decomposition: everything that is not the
    grid ledger, costs included. Summed over the 23 closed bots it reproduces
    the -760.62 headline exactly, so the trigger is measured on the same number
    the decision was taken on.
    """
    grid_profit = _finite(bot.get('grid_profit')) or 0.0
    inventory = ((_finite(bot.get('realized_pnl')) or 0.0)
                 + (_finite(bot.get('unrealized_pnl')) or 0.0) - grid_profit
                 - (_finite(bot.get('fees_paid')) or 0.0)
                 - (_finite(bot.get('funding_paid')) or 0.0))
    position = _finite(bot.get('position_contracts')) or 0.0
    per_line = _finite(bot.get('contracts_per_line')) or 0.0
    grids = bot.get('grids') if type(bot.get('grids')) is int else 0
    full_range = grids * per_line
    return dict(grid_profit=grid_profit, inventory_pnl=inventory,
                position_contracts=position, full_range_contracts=full_range,
                position_fraction=abs(position) / full_range if full_range > 0 else 0.0)


def cluster_distance_pct(bot, clusters, now_ms):
    """Distance to the nearest implied cluster on the side that hurts, or None.

    `clusters` is a radar row already annotated by radar.liquidity_levels (or
    any mapping carrying the same two fields). Missing, unreadable, stale or
    zero annotations return None, which fails the cluster gate closed rather
    than reading as "zero percent away".
    """
    if not isinstance(clusters, dict):
        return None
    generated = clusters.get('liq_clusters_generated_at_ms')
    if generated is not None:
        if isinstance(generated, bool) or not isinstance(generated, int) \
                or not 0 <= now_ms - generated <= CLUSTER_MAX_AGE_MS:
            return None
    position = _finite(bot.get('position_contracts')) or 0.0
    if not position:
        return None
    distance = _finite(clusters.get(LOSING_SIDE_FIELD[position > 0]))
    return distance if distance is not None and distance > 0 else None


def triggered(bot, clusters, now_ms, ratio, fraction, distance):
    """Evaluate one rule of the family; returns (fired, detail). Never mutates.

    ratio K:     inventory loss must exceed K x grid profit so far.
    fraction F:  |position| must reach F x the full-range position.
    distance D:  price must be within D% of the nearest cluster on the losing
                 side; None switches the cluster gate off entirely.
    """
    detail = metrics(bot)
    found = cluster_distance_pct(bot, clusters, now_ms)
    detail['cluster_distance_pct'] = found
    loss = -detail['inventory_pnl']
    detail['rule_inventory'] = int(loss > 0 and loss > ratio * max(detail['grid_profit'], 0.0))
    detail['rule_position'] = int(detail['position_fraction'] >= fraction)
    detail['rule_cluster'] = int(distance is None
                                 or (found is not None and found <= distance))
    fired = bool(detail['rule_inventory'] and detail['rule_position']
                 and detail['rule_cluster'])
    return fired, detail


def enabled(rules):
    """Fail-closed flag: only an explicit True in the learned store turns it on."""
    override = rules.get('hedge_enabled') if isinstance(rules, dict) else None
    return HEDGE_ENABLED if override is None else override is True


def hedgeable(bot):
    """A live, single-book, v2-shaped bot that actually carries inventory."""
    return (isinstance(bot, dict) and bot.get('closed_ms') is None
            and 'hedge_books' not in bot
            and bool(_finite(bot.get('position_contracts')))
            and type(bot.get('grids')) is int
            and bool(_finite(bot.get('contracts_per_line'))))


def should_hedge(bot, clusters, rules, now_ms):
    """(bool, detail) for the configured rule. Flag off means never, no matter what."""
    if not enabled(rules) or not hedgeable(bot):
        return False, {}
    return triggered(bot, clusters, now_ms, HEDGE_INVENTORY_RATIO,
                     HEDGE_POSITION_FRACTION, HEDGE_CLUSTER_DISTANCE_PCT)


def _paired_entry(order, lines):
    """Pre-v2 ledgers record only `paired_line`; the neutral books read `pair_entry`.

    The two spellings mean the same thing - v1 priced a closing pair at the
    paired grid line, v2 stores the actual entry - so the older ledger's grid
    economics are preserved exactly, not approximated.
    """
    if 'pair_entry' in order:
        return order
    paired = order.get('paired_line')
    return dict(order, pair_entry=None if paired is None else lines[paired])


def _grid_book(bot):
    """The original ladder, with its order dialect normalised for `step_neutral`."""
    book = deepcopy(bot)
    book['orders'] = [_paired_entry(order, bot['lines']) for order in bot['orders']]
    return book


def _offsetting_book(bot, price):
    """A mirror leg holding exactly the negative position and no ladder."""
    book = deepcopy(bot)
    book.pop('hedge_books', None)
    book.update(direction='SHORT' if bot['position_contracts'] > 0 else 'LONG',
                orders=[], position_contracts=0.0, avg_entry=0.0,
                realized_pnl=0.0, unrealized_pnl=0.0, fees_paid=0.0,
                funding_paid=0.0, fills=0, completed_grids=0, grid_profit=0.0)
    # Establishing the offsetting position is a market order, so it pays taker.
    engine._position_fill(book, -bot['position_contracts'], price, taker=True)
    return book


def hedge_leg(bot, price, now_ms):
    """Return a flat-net two-book copy of `bot` plus the HEDGE event.

    Book order is [long-side, short-side] because `neutral._sync` unpacks the
    two books into `long_contracts` / `short_contracts` positionally and signs
    grid profit by book index.
    """
    engine._number(price, positive=True)
    engine._timestamp(now_ms)
    if not hedgeable(bot):
        raise ValueError('bot cannot carry an offsetting hedge leg')
    detail = metrics(bot)
    result = deepcopy(bot)
    grid = _grid_book(result)
    offset = _offsetting_book(result, price)
    result['hedge_books'] = ([grid, offset] if result['position_contracts'] > 0
                             else [offset, grid])
    result['accounting_model'] = HEDGE_MODEL
    result['hedged_at_ms'] = now_ms
    _sync(result, price)
    event = engine._event(result, now_ms, 'HEDGE', price=price,
                          position_contracts=detail['position_contracts'],
                          grid_profit=detail['grid_profit'],
                          inventory_pnl=detail['inventory_pnl'])
    return result, [event]
