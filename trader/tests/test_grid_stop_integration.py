import unittest
from trader.research.kucoin_replay import _config as replay_config
from trader.research.kucoin_operator import _config as operator_config, validate_running
from trader.strategies.kucoin_grid import create_bot, advance, preview


class StopFormIntegrationTests(unittest.TestCase):
    def test_explicit_legacy_tick_aligned_stops_survive_both_config_adapters(self):
        self._assert_adapter_stops(.05, (80.88, 147.13))

    def test_default_exact_range_edges_survive_both_config_adapters(self):
        self._assert_adapter_stops(0, (85.13, 140.13))

    def _assert_adapter_stops(self, fraction, expected):
        policy = {} if fraction == 0 else dict(range_exit_stop_pct=.05, adaptive_range_stops=True)
        for direction in ('long', 'short', 'neutral'):
            form = dict(pair='TESTUSDTM', direction=direction, low=85.13,
                        high=140.13, grids=20, leverage=5, used_margin=1000,
                        reserved_margin=200, entry=100.13, quantity=.1,
                        multiplier=.1, lot_size=1, tick_size=.01,
                        stop_loss=147.13 if direction == 'short' else 80.88,
                        stop_loss_high=147.13 if direction == 'neutral' else None)
            configurations = (replay_config(dict(form, **policy)), operator_config(form, policy))
            for adapter, config in zip((replay_config, operator_config), configurations):
                with self.subTest(direction=direction, adapter=adapter.__module__):
                    self.assertEqual(config.tick_size, .01)
                    self.assertEqual(config.total_margin, 1200)
                    self.assertEqual(config.range_exit_stop_pct, fraction)
                    self.assertEqual(config.adaptive_range_stops, fraction != 0)
                    estimate = preview(config)
                    self.assertEqual(estimate['effective_stop_loss_low'], expected[0])
                    self.assertEqual(estimate['effective_stop_loss_high'], expected[1])
                    for price in expected:
                        state = advance(create_bot(config, 100.13, 0), price, 60000)
                        self.assertEqual(state.status, 'stopped')
                        self.assertEqual(state.price, price)
            form.update(bot_id='test', start_ms=0, expected_start_gph=4)
            self.assertEqual(len(validate_running(dict(schema_version=1, bots=[form]), 0, policy)), 1)
