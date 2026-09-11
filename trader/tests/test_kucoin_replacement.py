import unittest
from trader.research.kucoin_replacement import decide_replacement

HOUR = 3_600_000


def current(**changes):
    data = dict(pair='OLD', asof_ms=3*HOUR, price=100, low=90, high=110,
                direction='long', expected_start_gph=10, realized_gph_6h=1,
                floating_pnl=-45, close_fee=2, direction_flip=False,
                stop_loss_hit=False, status='running', running_pairs=['OLD', 'BUSY'])
    return dict(data, **changes)


def history(**changes):
    return [dict(asof_ms=t*HOUR, realized_gph_6h=1, direction_flip=False, **changes)
            for t in (2,)]


class ReplacementTests(unittest.TestCase):
    def test_table_of_causes(self):
        radar = [dict(pair='NEW', score=5, direction='long')]
        for changes, rows, reason in [({}, history(), 'low_grid_rate'),
              ({'price': 111, 'realized_gph_6h': 2}, [], 'range_exit'),
              ({'stop_loss_hit': True, 'status': 'stopped'}, [], 'stop_loss'),
              ({'direction_flip': True}, [dict(asof_ms=2*HOUR,
                  realized_gph_6h=10, direction_flip=True)], 'direction_flip')]:
            with self.subTest(reason=reason):
                result = decide_replacement(current(**changes), radar, rows)
                self.assertEqual(result['action'], 'replace')
                self.assertIn(reason, result['triggers'])
                self.assertEqual(result['switch_cost']['floating_pnl'], -45)
                self.assertEqual(result['switch_cost']['close_fees'], 2)
                self.assertEqual(result['switch_cost']['net_realized_on_close'], -47)
                self.assertEqual(result['switch_cost']['loss_cost'], 47)

    def test_consecutive_hours_are_required(self):
        for rows in ([], [dict(asof_ms=HOUR, realized_gph_6h=1)],
                     [dict(asof_ms=2*HOUR, realized_gph_6h=10)]):
            self.assertEqual(decide_replacement(current(), [], rows)['triggers'], [])

    def test_no_qualifier_keeps_or_waits_if_already_closed(self):
        for closed, action in [(False, 'keep'), (True, 'waiting')]:
            result = decide_replacement(current(stop_loss_hit=closed,
                status='stopped' if closed else 'running'),
                [dict(pair='NEW', score=2)], history())
            self.assertEqual(result['action'], action)
            self.assertIn('no qualifying', result['reason'])

    def test_go_down_radar_skip_running_and_strict_factor(self):
        rows = [dict(pair='BUSY', score=90), dict(pair='TIE', score=2),
                dict(pair='WIN', score=2.01)]
        result = decide_replacement(current(), rows, history())
        self.assertEqual(result['replacement']['pair'], 'WIN')

    def test_same_coin_new_direction_only_when_flip_held(self):
        candidate = dict(pair='OLD', direction='short', score=5)
        result = decide_replacement(current(direction_flip=True), [],
            [dict(asof_ms=2*HOUR, direction_flip=True, realized_gph_6h=10)],
            {'direction_flip_candidate': candidate})
        self.assertEqual(result['replacement']['direction'], 'short')

    def test_threshold_never_below_two(self):
        result = decide_replacement(current(expected_start_gph=1), [], [])
        self.assertEqual(result['minimum_gph'], 2)

    def test_future_and_duplicate_rows_cannot_fake_persistence(self):
        rows = [dict(asof_ms=4*HOUR, realized_gph_6h=0),
                dict(asof_ms=3*HOUR, realized_gph_6h=0)]
        self.assertEqual(decide_replacement(current(), [], rows)['triggers'], [])

    def test_closed_stop_loss_uses_actual_realized_close_cost(self):
        running = current(status='stopped', stop_loss_hit=True, floating_pnl=0,
                          close_fee=0, switch_close_gross_pnl=-45,
                          switch_close_fee_paid=2)
        cost = decide_replacement(running, [], [])['switch_cost']
        self.assertEqual(cost['net_realized_on_close'], -47)
        self.assertEqual(cost['floating_pnl'], -45)

    def test_preregistered_minimum_rate_factor_and_explicit_override(self):
        for options, expected in [({'minimum_rate_factor': .75}, 7.5),
                                  ({'minimum_rate_factor': .5}, 5),
                                  ({'minimum_rate_factor': .75, 'minimum_gph': 3}, 3)]:
            with self.subTest(options=options):
                result = decide_replacement(current(), [], [], options)
                self.assertEqual(result['minimum_gph'], expected)
