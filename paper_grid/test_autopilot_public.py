import copy
import unittest

from paper_grid import public_autopilot
from trader.autopilot import policy


class AutopilotPublicTests(unittest.TestCase):
    def source(self):
        return policy.snapshot(policy.new_state(1000), 2000,
                               dict(heartbeat_ms=2000, tick_age_s=10,
                                    kucoin_ok=True, radar_age_min=1))

    def test_allowlist_preserves_health_groups_and_drops_private_fields(self):
        source = self.source()
        source['private'] = 'do not publish'
        before = copy.deepcopy(source)
        result = public_autopilot.safe(source)
        self.assertNotIn('private', result)
        self.assertEqual(result['equity'], 10000)
        self.assertEqual(result['tick_age_s'], 10)
        self.assertEqual(set(result['groups']), {'LONG', 'SHORT', 'NEUTRAL'})
        self.assertEqual(source, before)

    def test_curve_is_validated_before_downsampling_and_bounds_preserved(self):
        source = self.source()
        source['equity_curve'] = [[i, 10000+i] for i in range(3000)]
        result = public_autopilot.safe(source)
        self.assertEqual(len(result['equity_curve']), 2000)
        self.assertEqual(result['equity_curve'][-1], [2999, 12999])
        source['equity_curve'][100][1] = float('nan')
        with self.assertRaises(ValueError):
            public_autopilot.safe(source)

    def test_watchlist_codes_and_history_are_closed_vocabularies(self):
        source = self.source()
        row = dict(symbol='RAYUSDTM', direction='NEUTRAL', score=71, since_ms=1,
                   rank=1, score_parts=[dict(code='OSCILLATION', value=18.4, points=27.6)])
        source['watchlist']['core'] = [row]
        source['watchlist_history'] = [dict(ts_ms=1, type='PROMOTE', symbol='RAYUSDTM',
            score=71, replaced_symbol='SAGAUSDTM', replaced_score=52, margin=19)] * 50
        result = public_autopilot.safe(source)
        self.assertEqual(len(result['watchlist_history']), 48)
        self.assertEqual(result['watchlist']['core'][0]['score'], 71)
        row['score_parts'][0]['code'] = 'private text'
        with self.assertRaises(ValueError):
            public_autopilot.safe(source)

    def test_rejects_bool_nonfinite_oversized_and_invalid_symbols(self):
        for key, value in [('equity', True), ('tick_age_s', float('inf'))]:
            source = self.source()
            source[key] = value
            with self.assertRaises(ValueError):
                public_autopilot.safe(source)
        source = self.source()
        source['open_bots'] = [{}] * 6
        with self.assertRaises(ValueError):
            public_autopilot.safe(source)

    def test_events_strip_unknown_numbers_and_reject_free_text(self):
        event = dict(ts_ms=1, bot_id=1, symbol='RAYUSDTM', type='FILL', price=1.5,
                     private_number=123)
        result = public_autopilot.events([event])
        self.assertNotIn('private_number', result[0])
        event['price'] = 'private text'
        with self.assertRaises(ValueError):
            public_autopilot.events([event])
        with self.assertRaises(ValueError):
            public_autopilot.events([event] * 501)

    def test_open_and_closed_bot_details_are_projected_without_engine_orders(self):
        from trader.papergrid.engine import open_bot, close_bot
        source = self.source()
        opened = open_bot(dict(bot_id=1, symbol='RAYUSDTM', direction='LONG',
            range_low=1, range_high=2, step_pct=.8, grids=20, notional_usdt=1000,
            leverage=3, funding_pct=0), 1.5, 1000)
        closed, _ = close_bot(opened, 1.6, 2000, 'MANUAL')
        source['open_bots'] = [opened]
        source['closed_bots'] = [closed]
        result = public_autopilot.safe(source)
        self.assertNotIn('orders', result['open_bots'][0])
        self.assertEqual(result['closed_bots'][0]['reason'], 'MANUAL')
        self.assertEqual(len(result['groups']['LONG']['open_bots']), 1)
        closed['reason'] = 'private reason'
        with self.assertRaises(ValueError):
            public_autopilot.safe(source)
