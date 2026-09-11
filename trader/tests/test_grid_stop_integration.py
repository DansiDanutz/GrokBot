import unittest
from trader.research.kucoin_replay import _config as replay_config
from trader.research.kucoin_operator import _config as operator_config, validate_running
from trader.strategies.kucoin_grid import create_bot, advance, preview


class StopFormIntegrationTests(unittest.TestCase):
    def test_tick_aligned_form_stops_survive_both_config_adapters(self):
        for direction in ('long', 'short', 'neutral'):
            form = dict(pair='TESTUSDTM', direction=direction, low=85.13,
                        high=140.13, grids=20, leverage=5, used_margin=1000,
                        reserved_margin=200, entry=100.13, quantity=.1,
                        multiplier=.1, lot_size=1, tick_size=.01,
                        stop_loss=147.13 if direction == 'short' else 80.88,
                        stop_loss_high=147.13 if direction == 'neutral' else None)
            for adapter in (replay_config, operator_config):
                config = adapter(form)
                with self.subTest(direction=direction, adapter=adapter.__module__):
                    self.assertEqual(config.tick_size, .01)
                    self.assertEqual(config.total_margin, 1200)
                    estimate = preview(config)
                    self.assertEqual(estimate['effective_stop_loss_low'], 80.88)
                    self.assertEqual(estimate['effective_stop_loss_high'], 147.13)
                    for price in (80.88, 147.13):
                        state = advance(create_bot(config, 100.13, 0), price, 60000)
                        self.assertEqual(state.status, 'stopped')
                        self.assertEqual(state.price, price)
            form.update(bot_id='test', start_ms=0, expected_start_gph=4)
            self.assertEqual(len(validate_running(dict(schema_version=1, bots=[form]), 0)), 1)
