import unittest
from unittest.mock import patch
from trader.research.kucoin_operator import operator_report, validate_running

HOUR = 3_600_000


def running(**changes):
    bot = dict(bot_id='test-bot', pair='TESTUSDTM', direction='long', leverage=5,
               used_margin=1000, reserved_margin=0, entry=100, low=90, high=110,
               grids=20, stop_loss=85, start_ms=0, expected_start_gph=10,
               quantity=1)
    return dict(schema_version=1, bots=[dict(bot, **changes)])


class MemorySnapshot:
    manifest = {'offline_copy': True, 'source': 'kucoin-public'}

    def records(self, asof, pairs=None):
        return []

    def candles(self, pair, start, end):
        return [dict(time_ms=t, open=100, high=100, low=100, close=100)
                for t in range(start, end, 60000)]

    def funding(self, pair, start, end):
        return [dict(time_ms=t, rate=.0001) for t in range(8*HOUR, end+1, 8*HOUR)
                if t > start]

    def market(self, pair, asof):
        return dict(bid=99.99, ask=100.01, observed_at_ms=asof,
                    book_observed_at_ms=asof)


def candidate(pair='NEW', score=10):
    return dict(pair=pair, direction='long', score=score, reason='fixture',
                setup=dict(running()['bots'][0], pair=pair,
                           preview={'profit_per_grid_min': 1}))


class OperatorTests(unittest.TestCase):
    def test_running_document_rejects_unknown_fields_credentials_and_bad_margin(self):
        for document in [dict(running(), api_key='not-a-credential'),
                         running(used_margin=999), running(start_ms=-1),
                         running(direction='up'), running(bot_id='../bad'),
                         running(secret='not-a-credential')]:
            with self.subTest(document=document):
                with self.assertRaises(ValueError):
                    validate_running(document, 2*HOUR)

    @patch('trader.research.kucoin_operator.radar')
    def test_hourly_tracking_has_full_ledger_and_held_replacement(self, mocked):
        mocked.return_value = dict(radar=[candidate()], rejected=[], coverage={})
        report = operator_report(MemorySnapshot(), 2*HOUR, running())
        self.assertFalse(report['live_use']['actionable'])
        self.assertIn('calibration', report['live_use']['status'])
        bot = report['running'][0]
        self.assertEqual([row['asof_ms'] for row in bot['hourly_history']], [HOUR, 2*HOUR])
        self.assertEqual(bot['verdict']['action'], 'replace')
        self.assertEqual(bot['verdict']['replacement']['pair'], 'NEW')
        self.assertGreater(len(bot['ledger']), 0)
        self.assertAlmostEqual(sum(row['fee'] for row in bot['ledger']), bot['tracker']['fees'])
        self.assertEqual(len(report['recommended_forms']), 1)
        self.assertEqual(report['recommended_forms'][0]['pair'], 'NEW')

    @patch('trader.research.kucoin_operator.radar')
    def test_canonical_form_preview_and_running_exclusion_are_preserved(self, mocked):
        mocked.return_value = dict(radar=[candidate('A'), candidate('B'), candidate('C')], rejected=[], coverage={})
        report = operator_report(MemorySnapshot(), HOUR, running())
        self.assertEqual(len(report['recommended_forms']), 2)
        self.assertEqual(report['recommended_forms'][0]['preview']['profit_per_grid_min'], 1)
        self.assertEqual(mocked.call_args.args[2], ['TESTUSDTM'])

    def test_missing_candles_and_funding_fail_closed(self):
        snapshot = MemorySnapshot()
        snapshot.candles = lambda *args: []
        with self.assertRaisesRegex(ValueError, 'candles'):
            operator_report(snapshot, HOUR, running())
        snapshot = MemorySnapshot()
        snapshot.funding = lambda *args: []
        with self.assertRaisesRegex(ValueError, 'funding'):
            operator_report(snapshot, 8*HOUR, running())

    def test_invalid_asof_and_future_bot_fail_before_reading(self):
        for at, document in [(1, running()), (HOUR, running(start_ms=2*HOUR))]:
            with self.assertRaises(ValueError):
                operator_report(None, at, document)

    def test_just_started_bot_retains_initial_seed_events(self):
        report = operator_report(MemorySnapshot(), 0, running())
        bot = report['running'][0]
        self.assertGreater(len(bot['ledger']), 0)
        self.assertAlmostEqual(sum(row['fee'] for row in bot['ledger']),
                               bot['tracker']['fees'])

    def test_running_form_rejects_leverage_outside_three_to_six(self):
        for leverage in (2, 7):
            with self.assertRaises(ValueError):
                validate_running(running(leverage=leverage), HOUR)

    def test_no_radar_replacement_after_stop_remains_waiting(self):
        snapshot = MemorySnapshot()
        snapshot.candles = lambda pair, start, end: [
            dict(time_ms=t, open=100, high=100, low=80, close=90)
            for t in range(start, end, 60000)]
        report = operator_report(snapshot, 2*HOUR, running())
        bot = report['running'][0]
        self.assertEqual(bot['verdict']['action'], 'waiting')
        self.assertTrue(bot['tracker']['stop_loss_hit'])
        self.assertLess(bot['verdict']['switch_cost']['net_realized_on_close'], 0)

    def test_switch_cost_uses_executable_quote_while_tracker_keeps_mark(self):
        snapshot = MemorySnapshot()
        snapshot.market = lambda pair, at: dict(bid=99.95, ask=100.05,
            observed_at_ms=at-1000, book_observed_at_ms=at-500)
        report = operator_report(snapshot, HOUR, running(grids=10))
        bot = report['running'][0]
        self.assertEqual(bot['tracker']['floating_pnl'], 0)
        cost = bot['verdict']['switch_cost']
        self.assertAlmostEqual(cost['floating_pnl'], -.25)
        self.assertAlmostEqual(cost['close_fees'], .29985)
        self.assertAlmostEqual(cost['net_realized_on_close'], -.54985)
        self.assertEqual(cost['close_cost_basis'], 'historical_bid_ask')
        self.assertEqual(cost['close_quote_bid'], 99.95)
        self.assertEqual(cost['quote_observation_age_ms'], 1000)

    def test_intrabar_stop_cost_coverage_is_explicitly_incomplete(self):
        snapshot = MemorySnapshot()
        snapshot.candles = lambda pair, start, end: [
            dict(time_ms=t, open=100, high=100, low=80, close=90)
            for t in range(start, end, 60000)]
        report = operator_report(snapshot, HOUR, running())
        self.assertFalse(report['coverage']['execution_costs_complete'])
        self.assertFalse(report['running'][0]['execution_cost_coverage']['complete'])
        self.assertIn('intrabar bid/ask', report['running'][0]['execution_cost_coverage']['reason'])
