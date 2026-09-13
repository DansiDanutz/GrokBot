"""Prospective compliance closes retain historical accounting and hold/risk rules."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from trader.autopilot import policy, setup_evidence
from trader.papergrid import open_bot
from trader.tests.test_autopilot_policy import row, radar

HOUR = 3600000


def legacy_state(valid=False):
    state, _ = policy.decide(policy.new_state(0), radar(long=[row('CAKEUSDTM')]), {}, 0, '0')
    wrapper = state['open_bots'][0]
    low, high, price = (80, 130, 100) if valid else (2.2009, 2.2676, 2.23)
    wrapper['engine'] = open_bot(dict(bot_id=1, symbol='CAKEUSDTM', direction='LONG',
        range_low=low, range_high=high, grids=12, grid_interval=(high-low)/12,
        step_pct=1, notional_usdt=1000, leverage=5, funding_pct=0,
        quantity_per_grid=1, accounting_version=2, funding_managed=True,
        fee_rate_maker=.0002, fee_rate_taker=.0006), price, 0)
    wrapper.pop('setup_evidence', None)
    return state


class LegacyGridComplianceTests(unittest.TestCase):
    def test_valid_legacy_stays_open_with_original_fees_and_missing_evidence(self):
        state = legacy_state(valid=True)
        before = deepcopy(state)
        with patch.object(policy, '_learned_rules', return_value={}):
            result, events = policy.decide(state, None, {'CAKEUSDTM': 100}, 1000, '1')
        self.assertEqual(state, before)
        self.assertEqual(len(result['open_bots']), 1)
        bot = result['open_bots'][0]['engine']
        self.assertTrue(setup_evidence.official_grid_return_valid(bot))
        self.assertEqual(bot['fee_rate_maker'], .0002)
        self.assertEqual(bot['fees_paid'], before['open_bots'][0]['engine']['fees_paid'])
        self.assertNotIn('setup_evidence', result['open_bots'][0])
        self.assertFalse(any(e['type'] == 'CLOSE' for e in events))

    def test_invalid_legacy_closes_as_profile_update_without_retroactive_fees(self):
        state = legacy_state()
        original = deepcopy(state['open_bots'][0]['engine'])
        self.assertFalse(setup_evidence.official_grid_return_valid(original))
        with patch.object(policy, '_learned_rules', return_value={}):
            result, _ = policy.decide(state, None, {'CAKEUSDTM': 2.23}, 1000, '1')
        self.assertEqual(result['open_bots'], [])
        bot = result['closed_bots'][0]['engine']
        self.assertEqual(bot['reason'], 'PROFILE_UPDATE')
        self.assertEqual(bot['fee_rate_maker'], .0002)
        self.assertEqual(bot['funding_paid'], original['funding_paid'])
        self.assertEqual(bot['realized_pnl'], original['realized_pnl'])
        self.assertAlmostEqual(bot['fees_paid']-original['fees_paid'],
            abs(original['position_contracts'])*2.23*original['fee_rate_taker'])

    def test_negative_legacy_profile_update_waits_for_learned_four_hour_hold(self):
        state = legacy_state()
        self.assertLess(policy.net(state['open_bots'][0]['engine']), 0)
        with patch.object(policy, '_learned_rules', return_value={'min_hold_hours_before_non_risk_close': 4}):
            held, _ = policy.decide(state, None, {'CAKEUSDTM': 2.23}, 4*HOUR-1, '1')
            self.assertEqual(len(held['open_bots']), 1)
            closed, _ = policy.decide(held, None, {'CAKEUSDTM': 2.23}, 4*HOUR, '2')
        self.assertEqual(closed['open_bots'], [])
        self.assertEqual(closed['closed_bots'][0]['engine']['reason'], 'PROFILE_UPDATE')

    def test_losing_range_exit_bypasses_hold_for_noncompliant_legacy(self):
        state = legacy_state()
        state['open_bots'][0]['signals'] = ['RANGE_BREAK']
        with patch.object(policy, '_learned_rules', return_value={'min_hold_hours_before_non_risk_close': 4}):
            result, _ = policy.decide(state, None, {'CAKEUSDTM': 2.19}, 1000, '1')
        self.assertEqual(result['open_bots'], [])
        bot = result['closed_bots'][0]['engine']
        self.assertEqual(bot['reason'], 'RANGE_BREAK')
        self.assertLess(policy.net(bot), 0)

    def test_actual_lines_and_direction_determine_compliance(self):
        # Gross gap gives LONG slightly over 1%, but SHORT/NEUTRAL below it.
        bot = dict(lines=[100, 100.3208], grids=1, leverage=5, direction='LONG',
                   grid_interval=100, fee_rate_maker=0)
        self.assertTrue(setup_evidence.official_grid_return_valid(bot))
        for direction in ('SHORT', 'NEUTRAL'):
            self.assertFalse(setup_evidence.official_grid_return_valid(dict(bot,direction=direction)))
        for lines in ([100,100], [100,float('nan')], [100,100.1], [100,101,102]):
            self.assertFalse(setup_evidence.official_grid_return_valid(dict(bot,lines=lines)))
        self.assertTrue(setup_evidence.official_grid_return_valid(dict(bot, grids=2, lines=[100,101,102])))
        self.assertFalse(setup_evidence.official_grid_return_valid(dict(bot, grids=2, lines=[100,101,101.1])))


if __name__ == '__main__':
    unittest.main()
