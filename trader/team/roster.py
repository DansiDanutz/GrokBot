"""Every participant of the circle, what it watches, and how Dan links it.

Ten of them live in the native Grok Bot desktop app and can only read public
URLs; the rest are deterministic jobs on this machine. A role is described once,
here, so the controller, the doctor and the documentation cannot disagree about
who exists, what wakes them and how long silence is still acceptable.
"""
from types import MappingProxyType

PUBLIC = 'https://danslabtrader.vercel.app/data'
TEAM_URL = PUBLIC + '/team.json'
AUTOPILOT_URL = PUBLIC + '/autopilot.json'
RADAR_URL = PUBLIC + '/radar.json'

TRADING_ROOM = 'Paper Grid Trading Team'
RESEARCH_ROOM = 'Paper Grid Research & Data'
OFFICE_ROOM = 'Paper Desk Office'

NATIVE_IDLE_H = 24.0          # a review role answers at least once a day
STEWARD_IDLE_H = 6.0          # the only writer of the board; silence hurts fast

REVIEW_READS = (TEAM_URL, AUTOPILOT_URL, RADAR_URL)


def linking(name, room):
    """The exact paragraph Dan pastes into that bot's native instructions."""
    return ('Team link. Every cycle, read %s and take only the dispatches whose '
            'role is "%s". Answer each one in %s with its dispatch_id alone on '
            'the first line, then at most five bullets. Do not answer another '
            "role's dispatch and do not start a new round; Dan's Senior "
            'Developer records the receipt for each answer you post.'
            % (TEAM_URL, name, room))


def _role(id, name, kind, rooms, reads, triggers, max_idle_h,
          installed=True, room=None):
    link = linking(name, room) if room else ''
    return MappingProxyType(dict(
        id=id, name=name, kind=kind, rooms=tuple(rooms), reads=tuple(reads),
        triggers=tuple(triggers), max_idle_h=max_idle_h, installed=installed,
        linking=link))


def _native(id, name, rooms, triggers, room, installed=True):
    return _role(id, name, 'native', rooms, REVIEW_READS, triggers,
                 NATIVE_IDLE_H, installed=installed, room=room)


def _job(id, name, reads, triggers, max_idle_h):
    return _role(id, name, 'deterministic', (), reads, triggers, max_idle_h)


ROLES = (
    _native('grid_desk_lead', 'Grid Desk Lead',
            (TRADING_ROOM, RESEARCH_ROOM, OFFICE_ROOM),
            ('ROLE_IDLE', 'CONTROLLER_RESUMED', 'SYNTHESIS'), TRADING_ROOM),
    _native('data_structure', 'Data & Structure', (TRADING_ROOM,),
            ('RADAR_SCAN',), TRADING_ROOM),
    _native('risk_sentinel', 'Risk Sentinel', (TRADING_ROOM,),
            ('DOCTOR_FAIL', 'DOCTOR_RECOVERED', 'ENTRIES_STALLED',
             'STRUCTURE_BLACKOUT'), TRADING_ROOM),
    _native('performance_analyst', 'Performance Analyst', (TRADING_ROOM,),
            ('BOT_CLOSED', 'COUNTERFACTUAL_READY'), TRADING_ROOM),
    _native('research_scout', 'Research Scout', (RESEARCH_ROOM,),
            ('RESEARCH_DUE',), RESEARCH_ROOM),
    _native('x_setup_researcher', 'X Setup Researcher', (RESEARCH_ROOM,),
            ('RESEARCH_DUE',), RESEARCH_ROOM),
    _native('strategy_manager', 'Strategy Manager', (TRADING_ROOM, RESEARCH_ROOM),
            ('LEARNER_DEFERRED', 'LEARNER_APPLIED', 'COUNTERFACTUAL_READY',
             'ENGINEERING_DUE'), TRADING_ROOM),
    _native('technical_interpreter', 'Technical Interpreter',
            (TRADING_ROOM, RESEARCH_ROOM),
            ('BOT_OPENED', 'LIQ_CLUSTERS_READY'), TRADING_ROOM),
    # Dan has not added this bot in the app yet; the controller must say so
    # rather than quietly counting a role that cannot answer.
    _native('discovery_auditor', 'Discovery Auditor', (OFFICE_ROOM,),
            ('DISCOVERY',), OFFICE_ROOM, installed=False),
    _role('senior_developer', "Dan's Senior Developer", 'steward',
          (RESEARCH_ROOM, OFFICE_ROOM),
          ('team-evidence/board.json', 'team-evidence/receipts/',
           'autopilot/autopilot.json', 'market-data/market.sqlite3'),
          ('DOCTOR_FAIL', 'ENTRIES_STALLED', 'STRUCTURE_BLACKOUT',
           'DATA_PHASE_DUE', 'CONTROLLER_RESUMED'),
          STEWARD_IDLE_H, room=RESEARCH_ROOM),
    _role('paper_desk_secretary', 'Paper Desk Secretary', 'secretary',
          (OFFICE_ROOM,), REVIEW_READS, ('FINAL_RESPONSE',),
          NATIVE_IDLE_H, room=OFFICE_ROOM),
    _job('market_data', 'Market data collector', ('market-data/market.sqlite3',),
         (), 1.0),
    _job('radar', 'Radar', ('radar/radar.json',), ('RADAR_SCAN',), 2.0),
    _job('autopilot', 'Autopilot', ('autopilot/autopilot.json',),
         ('BOT_OPENED', 'BOT_CLOSED', 'ENTRIES_STALLED', 'STRUCTURE_BLACKOUT'), 0.1),
    _job('publisher', 'Publisher', ('vercel-publisher/publisher.json',), (), 0.5),
    _job('coinglass', 'CoinGlass history', ('market-data/logs/coinglass.out.log',),
         (), 3.0),
    _job('liq_clusters', 'Liquidation clusters',
         ('market-data/liquidation-clusters.json',), ('LIQ_CLUSTERS_READY',), 3.0),
    _job('daily_review', 'Daily review', ('autopilot/review-status.json',),
         ('LEARNER_DEFERRED', 'LEARNER_APPLIED'), 26.0),
    _job('counterfactual', 'Counterfactual replay', ('reports/counterfactual.json',),
         ('COUNTERFACTUAL_READY',), 26.0),
    _job('doctor', 'Doctor', ('autopilot/doctor-state.json',),
         ('DOCTOR_FAIL', 'DOCTOR_RECOVERED'), 1.0),
    _job('controller', 'Team controller', ('team-evidence/controller-state.json',),
         ('ROLE_IDLE', 'CONTROLLER_RESUMED', 'DATA_PHASE_DUE', 'RESEARCH_DUE',
          'ENGINEERING_DUE'), 2.0),
)

BY_ID = MappingProxyType({role['id']: role for role in ROLES})
NAMES = MappingProxyType({role['id']: role['name'] for role in ROLES})
NATIVE_KINDS = ('native', 'steward', 'secretary')


def native():
    """Roles that live in the Grok Bot app and need a linking paragraph."""
    return tuple(r for r in ROLES if r['kind'] in NATIVE_KINDS)


def room_of(role_id):
    rooms = BY_ID[role_id]['rooms']
    return rooms[0] if rooms else ''


def max_idle_h(role_id):
    return BY_ID[role_id]['max_idle_h']
