"""Paper-only portfolio and scheduling constants from the approved spec."""
PAPER_EQUITY_USDT = 10_000
CORE_SIZE = 5
BENCH_SIZE = 5
PROMOTION_MARGIN = 10
CORE_MIN_HOLD_HOURS = 2
MAX_SWAPS_PER_SCAN = 1
MAX_BOTS = CORE_SIZE
NOTIONAL_PER_BOT_USDT = 1_000
SLOTS = {'NEUTRAL': 2, 'LONG': 2, 'SHORT': 2}
DIRECTION_CAP = 4
LEVERAGE_TREND = 5
LEVERAGE_NEUTRAL = 5
STEP_NEUTRAL_PCT = .45
RESERVE_PER_BOT_USDT = 200
NEUTRAL_RESERVE_USDT = RESERVE_PER_BOT_USDT
MAJORS_MAX = 1
MOVERS_MAX = 1
MAJORS = ('XBTUSDTM', 'ETHUSDTM', 'SOLUSDTM')
MIN_EXPECTED_GRIDS_PER_HOUR = 2.0
COOLDOWN_HOURS = 6
MAX_AGE_HOURS = 72
# Non-risk closes (LABEL_FLIP, DROPPED, MAX_AGE) only fire when a better coin is
# free to take the slot. Risk closes and PROFILE_UPDATE stay unconditional.
# Kill switch: set False (or the learned rule opportunity_cost_close) to restore
# the unconditional close path.
OPPORTUNITY_COST_CLOSE = True
# Ceiling on that deferral, for MAX_AGE only: a bot that has outlived its thesis
# leaves even when the market offers nothing better, so a thin market cannot hold
# a stale position open forever. LABEL_FLIP and DROPPED are the radar changing
# its opinion, not the bot going stale, and keep deferring.
OPPORTUNITY_HOLD_MAX_AGE_HOURS = 2 * MAX_AGE_HOURS
# Agent influence over entry ordering (T8). The desk reads one sanitized file
# written by the team's operations steward; agents may reorder admitted
# candidates (BOOST) or remove one for a scan (VETO) and nothing else. Ships
# dark: the owner turns it on here, or the learned rule agent_influence_enabled
# overrides it. OFF means the file is never even read.
AGENT_INFLUENCE_ENABLED = False
# Per-candidate ceiling on the ordering delta one scan may grant, in score
# points, and the ceiling on the sum granted across the whole scan. Over-cap
# values clamp (and are logged); they never reject the file.
INFLUENCE_MAX_DELTA = 10.0
INFLUENCE_MAX_TOTAL_DELTA = 20.0
# A forgotten file must not steer the desk for days: records older than this
# are ignored entirely.
INFLUENCE_MAX_AGE_MIN = 90
TICK_INTERVAL_S = 10
DECISION_INTERVAL_S = 300
SNAPSHOT_MAX_INTERVAL_S = 30
TICK_STALE_ALERT_S = 180
KUCOIN_DOWN_ALERT_S = 300
# A running daemon that admits nothing is a failure the liveness checks above
# cannot see. A scan this wide that verifies no structure at all is a plumbing
# fault, never a market condition; a shorter scan may simply be partial.
BLACKOUT_MIN_ROWS = 50
ENTRY_STALL_ALERT_H = 3
# System ERROR event codes (type ERROR, bot_id 0, symbol SYSTEM) in events.jsonl.
LOCAL_ERROR = 5  # policy/engine failure after a successful transport pass;
                 # kucoin_ok stays unchanged and the tick continues.

# T5 hedge trigger. Ships dark: HEDGE_ENABLED stays False until the owner has
# read docs/hedge-trigger.md and turns it on (or sets the learned-rule override
# `hedge_enabled`). Thresholds are the rule family the backtest selected.
HEDGE_ENABLED = False
HEDGE_INVENTORY_RATIO = 2.0        # inventory loss must exceed K x grid profit
HEDGE_POSITION_FRACTION = 0.5      # |position| must reach F x full-range position
HEDGE_CLUSTER_DISTANCE_PCT = None  # D: cluster proximity gate; None disables it
INFLUENCE_ERROR = 6  # the agent-influence file was rejected (any fail-closed
                     # case); the desk traded deterministically for that scan.
