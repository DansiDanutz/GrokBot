"""Which role answers which event, and what it is told to do.

One table. If an event is not here it reaches nobody, which is exactly the
failure this layer exists to prevent, so the roster tests read this table.
"""
from trader.team import events, roster

ROUTES = {
    'DOCTOR_FAIL': ('risk_sentinel', 'senior_developer'),
    'DOCTOR_RECOVERED': ('risk_sentinel',),
    'RADAR_SCAN': ('data_structure',),
    'BOT_OPENED': ('technical_interpreter',),
    'BOT_CLOSED': ('performance_analyst',),
    'ENTRIES_STALLED': ('senior_developer', 'risk_sentinel'),
    'STRUCTURE_BLACKOUT': ('senior_developer', 'risk_sentinel'),
    'LEARNER_DEFERRED': ('strategy_manager',),
    'LEARNER_APPLIED': ('strategy_manager',),
    'COUNTERFACTUAL_READY': ('strategy_manager', 'performance_analyst'),
    'LIQ_CLUSTERS_READY': ('technical_interpreter',),
    'RESEARCH_DUE': ('x_setup_researcher', 'research_scout'),
    'DATA_PHASE_DUE': ('senior_developer',),
    'ENGINEERING_DUE': ('strategy_manager',),
    'ROLE_IDLE': ('grid_desk_lead',),
    'CONTROLLER_RESUMED': ('grid_desk_lead', 'senior_developer'),
}

# Standing dispatches the controller raises itself, outside the event stream.
STANDING = ('SYNTHESIS', 'FINAL_RESPONSE', 'DISCOVERY')
STANDING_ROLE = dict(SYNTHESIS='grid_desk_lead',
                     FINAL_RESPONSE='paper_desk_secretary',
                     DISCOVERY='discovery_auditor')
KNOWN = tuple(events.EVENTS) + STANDING

# Cheapest thing to lose when the open-dispatch cap bites is the last entry.
PRIORITY = ('DOCTOR_FAIL', 'STRUCTURE_BLACKOUT', 'ENTRIES_STALLED',
            'CONTROLLER_RESUMED', 'DATA_PHASE_DUE', 'RESEARCH_DUE',
            'ENGINEERING_DUE', 'ROLE_IDLE', 'LEARNER_APPLIED', 'LEARNER_DEFERRED',
            'COUNTERFACTUAL_READY', 'LIQ_CLUSTERS_READY', 'BOT_CLOSED',
            'BOT_OPENED', 'RADAR_SCAN', 'DOCTOR_RECOVERED')


def ranked(fired):
    """Stable order: the events that cannot wait are routed first."""
    rank = {name: index for index, name in enumerate(PRIORITY)}
    return sorted(fired, key=lambda item: rank.get(item['name'], len(PRIORITY)))


# Events whose outcome the user is entitled to hear about.
USER_FACING = ('BOT_CLOSED', 'DOCTOR_FAIL', 'DATA_PHASE_DUE', 'RESEARCH_DUE',
               'ENGINEERING_DUE')

TEMPLATES = {
    'DOCTOR_FAIL': 'Doctor check {check} is failing ({detail}). Say what it puts '
                   'at risk for the open positions and the one number that decides it.',
    'DOCTOR_RECOVERED': 'The doctor is clear again. Confirm from the public '
                        'snapshots that no open position was harmed while it failed.',
    'RADAR_SCAN': 'Radar scan {scan_id} carries {candidates} core candidates. '
                  'Report structure freshness and which candidates have confirmed bounds.',
    'BOT_OPENED': 'Bot {bot_id} opened {symbol} {direction}. Verify the entry '
                  'evidence: complete-bar window, confirmed bounds, tick-rounded '
                  'interval and the original entry split.',
    'BOT_CLOSED': 'Bot {bot_id} closed {symbol} ({reason}). Reconcile net after '
                  'fees and funding against gross grid profit and say what the close cost.',
    'ENTRIES_STALLED': 'Entries are stalled with free slots and live candidates. '
                       'Name the blocking rule and the evidence that would clear it.',
    'STRUCTURE_BLACKOUT': 'Structure verification is blacked out. Report which '
                          'source is missing and whether any open bot is now unbounded.',
    'LEARNER_DEFERRED': 'The daily review deferred: {headline}. Record the stage '
                        'and the exact gate evidence still missing.',
    'LEARNER_APPLIED': 'The daily review applied a rule. Confirm the active rule '
                       'and a post-change observation before calling it verified.',
    'COUNTERFACTUAL_READY': 'Counterfactual replay for {date} is ready. Compare '
                            'skipped decisions against realised outcomes; propose no rule change yet.',
    'LIQ_CLUSTERS_READY': 'New liquidation clusters are published. Normalise '
                          'venue, scope and as-of before comparing them to our ranges.',
    'RESEARCH_DUE': 'Daily research pass, due {due_local} and {overdue_h}h late. '
                    'Bring at most one falsifiable finding with source, date and its limitation.',
    'DATA_PHASE_DUE': 'Daily data phase, due {due_local} and {overdue_h}h late. '
                      'Publish one sanitized market packet with source times, coverage and gaps.',
    'ENGINEERING_DUE': 'Engineering phase, due {due_local}. Bridge request: '
                       '{request_id}. Record the stage from its receipt; applied stays false.',
    'ROLE_IDLE': '{role} has not answered for {idle_h} hours. Reassign its work '
                 'or record the blocker on the board.',
    'CONTROLLER_RESUMED': 'The controller resumed after {gap_h}h without a cycle. '
                          'Recover the board and reconcile every dispatch still open.',
    'SYNTHESIS': 'Close this cycle with one synthesis over {count} dispatch(es): '
                 'evidence window, net outcome, compliance, unresolved disagreement, one advisory.',
    'FINAL_RESPONSE': 'Give the user one final answer covering {outcomes}. Name '
                      'any blocker and its owner; do not duplicate an earlier answer.',
    'DISCOVERY': 'Daily system audit. Gaps the controller can already see: '
                 '{gaps}. Propose each new bot setup or integration as a NEED- '
                 'item with an owner and an acceptance measure.',
}


class _Blank(dict):
    def __missing__(self, key):
        return 'unknown'


def instruction(name, payload):
    """Short, per-event text; a missing payload field never raises."""
    template = TEMPLATES.get(name)
    if template is None:
        return 'Answer this dispatch in your room with its dispatch_id first.'
    return template.format_map(_Blank(payload or {}))


def roles_for(name, payload):
    """Role ids that must answer this event, in table order."""
    if name == 'RADAR_SCAN' and not (payload or {}).get('candidates_changed'):
        return ()                      # a repeat scan with the same candidates
    return tuple(role for role in ROUTES.get(name, ())
                 if roster.BY_ID[role]['installed'])
