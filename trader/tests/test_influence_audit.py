"""Offline contracts for pricing agent influence against the replay (T8).

`trader.autopilot.influence_audit.summarize` joins every applied influence to
the counterfactual outcome of the entry it turned away, so the daily review can
answer "did this agent's say help or hurt, in USDT".
"""
from pathlib import Path
import tempfile
import unittest

from trader.autopilot import influence_audit, policy
from trader.data.store import Store
from trader.review import daily
from trader.review import rules as learned_rules

NOW = 1_789_200_000_000
AGENT = 'risk_sentinel'
SCOUT = 'x_setup_researcher'


def influence_event(agent, verb, symbol='A', direction='LONG', ts_ms=NOW,
                    delta=5.0, reason_code=1, clamped=0):
    code = policy.DECISION_RULES['influence_veto' if verb == 'VETO' else 'influence_boost']
    return dict(ts_ms=ts_ms, bot_id=0, symbol=symbol, type='DECISION',
                action='influence', direction=direction, radar_direction=direction,
                radar_score=50.0, expected_grids_per_hour=4.0, range_width_pct=10.0,
                funding_rate=0.0, kucoin_ok=1, rule_blocks=[code], agent_id=agent,
                reason_code=reason_code, influence_delta=delta,
                influence_clamped=clamped)


def outcome(symbol='A', direction='LONG', ts_ms=NOW, net=40.0):
    return dict(symbol=symbol, direction=direction, ts_ms=ts_ms, net=net)


class AuditJoinTests(unittest.TestCase):
    def test_a_veto_that_turned_away_a_profitable_entry_is_a_cost(self):
        report = influence_audit.summarize([influence_event(AGENT, 'VETO')],
                                           [outcome(net=40.0)])
        self.assertEqual(report['agents'][AGENT]['net_usdt'], -40.0)
        self.assertEqual(report['agents'][AGENT]['vetoes'], 1)
        self.assertEqual(report['agents'][AGENT]['matched_vetoes'], 1)
        self.assertEqual(report['total']['net_usdt'], -40.0)

    def test_a_veto_that_avoided_a_loss_is_a_saving(self):
        report = influence_audit.summarize([influence_event(AGENT, 'VETO')],
                                           [outcome(net=-31.5)])
        self.assertEqual(report['agents'][AGENT]['net_usdt'], 31.5)

    def test_the_join_needs_the_same_scan_symbol_and_direction(self):
        events = [influence_event(AGENT, 'VETO')]
        for other in (outcome(ts_ms=NOW + 1), outcome(symbol='B'),
                      outcome(direction='SHORT')):
            report = influence_audit.summarize(events, [other])
            self.assertEqual(report['total']['matched_vetoes'], 0)
            self.assertEqual(report['total']['net_usdt'], 0.0)

    def test_boosts_are_counted_but_never_priced(self):
        report = influence_audit.summarize(
            [influence_event(SCOUT, 'BOOST', clamped=1)], [outcome(net=40.0)])
        self.assertEqual(report['agents'][SCOUT]['boosts'], 1)
        self.assertEqual(report['agents'][SCOUT]['clamped'], 1)
        self.assertEqual(report['agents'][SCOUT]['net_usdt'], 0.0)

    def test_agents_are_scored_separately_and_records_are_listed(self):
        report = influence_audit.summarize(
            [influence_event(AGENT, 'VETO'), influence_event(SCOUT, 'VETO', symbol='B')],
            [outcome(net=30.0), outcome(symbol='B', net=-10.0)])
        self.assertEqual(report['agents'][AGENT]['net_usdt'], -30.0)
        self.assertEqual(report['agents'][SCOUT]['net_usdt'], 10.0)
        self.assertEqual(report['total']['net_usdt'], -20.0)
        self.assertEqual([r['symbol'] for r in report['records']], ['A', 'B'])

    def test_non_influence_events_and_bad_outcomes_are_skipped(self):
        noise = [{'type': 'OPEN', 'ts_ms': NOW}, {'type': 'DECISION', 'action': 'skip'}]
        report = influence_audit.summarize(noise + [influence_event(AGENT, 'VETO')],
                                           [outcome(net='oops'), outcome(net=5.0)])
        self.assertEqual(report['total']['influences'], 1)
        self.assertEqual(report['agents'][AGENT]['net_usdt'], -5.0)

    def test_an_empty_day_is_empty(self):
        self.assertEqual(influence_audit.summarize([], []),
                         {'agents': {}, 'total': influence_audit._blank(), 'records': []})


class AdvisoryTests(unittest.TestCase):
    def summary(self, net):
        return influence_audit.summarize(
            [influence_event(AGENT, 'VETO')], [outcome(net=net)])

    def test_the_advisory_fires_only_past_the_threshold(self):
        threshold = learned_rules.INFLUENCE_COST_USD
        self.assertEqual(learned_rules.influence_proposals(self.summary(threshold)), [])
        notes = learned_rules.influence_proposals(self.summary(threshold + 0.01))
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0]['rule'], 'influence_cost')
        self.assertEqual(notes[0]['tier'], 2)
        self.assertEqual(notes[0]['agent_id'], AGENT)
        self.assertIn(AGENT, notes[0]['evidence'])

    def test_a_profitable_agent_never_fires(self):
        self.assertEqual(learned_rules.influence_proposals(self.summary(-100.0)), [])

    def test_a_missing_or_empty_report_is_silent(self):
        for value in (None, {}, {'agents': None}, {'agents': {'x': None}}):
            self.assertEqual(learned_rules.influence_proposals(value), [])

    def test_the_advisory_never_auto_applies(self):
        notes = learned_rules.influence_proposals(self.summary(500.0))
        self.assertEqual(notes[0]['action'], 'review')
        self.assertEqual(learned_rules.plan_changes({}, {}, '2026-09-13'), [])


class DailyReviewSectionTests(unittest.TestCase):
    """The 07:00 review gains one additive section and one tier-2 advisory."""

    DATE = '2026-09-13'

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        database = Path(self.temp.name).resolve() / 'market.sqlite3'
        with Store(database):
            pass
        self.conn = daily.open_market_db(database)
        self.addCleanup(self.conn.close)

    def build(self, events=(), net=None):
        replay = None if net is None else {'outcomes': [outcome(net=net)],
                                           'summary': {}, 'replayed': 0}
        return daily.build_report(self.DATE, {'open_bots': [], 'closed_bots': []},
                                  self.conn, list(events), counterfactual=replay)

    def test_a_silent_day_leaves_the_report_unchanged(self):
        data = self.build()
        self.assertEqual(data['influence']['total']['influences'], 0)
        self.assertNotIn('Agent influence, priced', daily.render_markdown(data))

    def test_the_section_renders_the_per_agent_ledger(self):
        data = self.build([influence_event(AGENT, 'VETO'),
                           influence_event(SCOUT, 'BOOST', symbol='B')], net=40.0)
        markdown = daily.render_markdown(data)
        self.assertIn('Agent influence, priced', markdown)
        self.assertIn(AGENT, markdown)
        self.assertIn(SCOUT, markdown)
        self.assertIn('-40.00', markdown)

    def test_the_advisory_reaches_the_proposals_as_tier_2(self):
        data = self.build([influence_event(AGENT, 'VETO')], net=40.0)
        notes = [p for p in data['proposals'] if p['rule'] == 'influence_cost']
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0]['tier'], 2)
        self.assertIn('influence channel off', daily.render_markdown(data))

    def test_a_cheap_day_raises_no_advisory(self):
        data = self.build([influence_event(AGENT, 'VETO')], net=1.0)
        self.assertEqual([p for p in data['proposals'] if p['rule'] == 'influence_cost'], [])


if __name__ == '__main__':
    unittest.main()
