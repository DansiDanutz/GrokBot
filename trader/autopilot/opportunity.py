"""Opportunity-cost gate for non-risk closes (owner decision, 2026-09-13).

A losing bot whose grids still work is hedged first; the only thing a non-risk
close buys is the slot it frees. This module answers the single question that
justifies such a close -- would a strictly better coin take the freed slot right
now? -- and nothing else.

Pure: nothing here mutates the caller's state. Admission reuses policy's own
helpers so there is exactly one definition of "eligible candidate"; the slot is
evaluated as already free, because the bot under consideration would be closed.
"""
from trader.autopilot import policy
from trader.autopilot.constants import PROMOTION_MARGIN

# decide() fills a vacancy with its own direction first, then borrows in this order.
FALLBACK_DIRECTIONS = ('NEUTRAL', 'LONG', 'SHORT')


def _slot_directions(wrapper):
    """The slot direction, then every direction the freed slot could borrow."""
    slot = wrapper.get('slot_direction') or wrapper['engine']['direction']
    return (slot, *(name for name in FALLBACK_DIRECTIONS if name != slot))


def _freed_state(state, wrapper):
    """The portfolio as it would be with this bot closed; the caller's state is untouched."""
    return dict(state, open_bots=[item for item in state['open_bots'] if item is not wrapper])


def opening_score(wrapper):
    """The radar score the bot was admitted on; 0.0 for pre-decision-context bots."""
    context = wrapper.get('decision_context') or {}
    try:
        return float(context.get('radar_score') or 0.0)
    except (TypeError, ValueError):
        return 0.0


def best_candidate_score(state, sections, wrapper, labels, now_ms, rules):
    """Highest watchlist score among rows that could legally take the freed slot.

    Returns None when no row qualifies. Open symbols (including this bot's own,
    which enters cooldown the moment it closes) are never candidates.
    """
    freed = _freed_state(state, wrapper)
    taken = {item['engine']['symbol'] for item in state['open_bots']}
    best = None
    for direction in _slot_directions(wrapper):
        for section, row in policy._candidates(sections, direction):
            score = float(row.get('score') or 0.0)
            if row['symbol'] in taken or (best is not None and score <= best):
                continue
            if policy._trend_gate_blocks(direction, row, labels, rules):
                continue
            if policy._cooldown_gate_blocks(row, rules, now_ms):
                continue
            if not policy._eligibility(freed, row, direction, section, now_ms)[0]:
                continue
            best = score
    return best


def better_candidate_exists(state, sections, wrapper, labels, now_ms, rules):
    """(bool, detail) -- is a strictly better coin waiting for this bot's slot?

    True when a qualified candidate's score beats the bot's opening score by at
    least PROMOTION_MARGIN, the same margin the watchlist demands for a swap.
    detail carries numeric fields only, so it can be logged as an event.
    """
    bot_score = opening_score(wrapper)
    best = best_candidate_score(state, sections, wrapper, labels, now_ms, rules)
    candidate_score = 0.0 if best is None else best
    detail = dict(candidate_score=candidate_score, bot_score=bot_score,
                  margin=candidate_score - bot_score)
    return bool(best is not None and detail['margin'] >= PROMOTION_MARGIN), detail
