"""A boundary close taken during recovery must stop gating the desk once the
public candle record proves the window was covered. Before 2026-09-18 the
pending map had a writer but no reader, so one such close (bot 56) froze all
future entries permanently."""
import tempfile
import unittest
from pathlib import Path

from trader.autopilot.market import minute_times
from trader.autopilot.runtime import Runner
from trader.autopilot.storage import atomic_json
from trader.data.store import Store
from trader.tests.test_autopilot_runtime import Client, NOW
from trader.tests.test_autopilot_policy import radar, row

MINUTE = 60000


def candle(symbol, time_ms):
    return dict(symbol=symbol, interval='1m', time_ms=time_ms,
                open=1.0, high=1.1, low=0.9, close=1.0, volume=10.0, turnover=10.0)


class ReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.database = self.root / 'market.sqlite3'
        with Store(self.database):
            pass
        self.clock = [NOW]
        self.client = Client(self.clock)
        self.radar = self.root / 'radar.json'
        report = radar(neutral=[row('A', 'NEUTRAL')])
        report.update(schema_version=1, asof_ms=NOW)
        atomic_json(self.radar, report)
        self.state = self.root / 'autopilot' / 'state.json'
        self.snapshot = self.root / 'autopilot.json'

    def runner(self):
        return Runner(self.database, self.state, self.radar, self.snapshot,
                      client=self.client, now_ms=lambda: self.clock[0])

    def pending(self, runner, last_processed, observed):
        runner.state['runtime']['pending_recovery_reconciliation']['56'] = dict(
            bot_id=56, symbol='AUSDTM', status='PENDING_RECONCILIATION',
            reason='RECOVERY_COVERAGE_UNVERIFIED',
            last_processed_ms=last_processed, observed_ms=observed,
            boundary_price=1.0, update_kind='candle')

    def test_minute_times_reads_committed_candles(self):
        with Store(self.database) as store:
            store.upsert('klines', [candle('AUSDTM', NOW), candle('AUSDTM', NOW + MINUTE)])
        self.assertEqual(minute_times(self.database, 'AUSDTM', NOW, NOW + 2 * MINUTE),
                         {NOW, NOW + MINUTE})

    def test_verified_coverage_lifts_the_gate(self):
        with Store(self.database) as store:
            store.upsert('klines', [candle('AUSDTM', NOW)])
        runner = self.runner()
        self.pending(runner, NOW, NOW + MINUTE)
        runner._reconcile_recovery(NOW + 2 * MINUTE)
        self.assertEqual(runner.state['runtime']['pending_recovery_reconciliation'], {})

    def test_unproven_coverage_keeps_the_gate(self):
        runner = self.runner()
        self.pending(runner, NOW, NOW + MINUTE)  # no candle committed
        runner._reconcile_recovery(NOW + 2 * MINUTE)
        self.assertIn('56', runner.state['runtime']['pending_recovery_reconciliation'])

    def test_resolution_is_recorded_on_the_closed_bot(self):
        with Store(self.database) as store:
            store.upsert('klines', [candle('AUSDTM', NOW)])
        runner = self.runner()
        runner.state['closed_bots'].append(dict(engine=dict(bot_id=56, symbol='AUSDTM')))
        self.pending(runner, NOW, NOW + MINUTE)
        runner._reconcile_recovery(NOW + 2 * MINUTE)
        recorded = runner.state['closed_bots'][-1]['recovery_reconciliation']
        self.assertEqual(recorded['status'], 'RESOLVED')
        self.assertEqual(recorded['reason'], 'COVERAGE_VERIFIED')

    def test_sub_minute_window_still_needs_its_enclosing_candle(self):
        runner = self.runner()
        self.pending(runner, NOW, NOW + 1000)
        runner._reconcile_recovery(NOW + MINUTE)
        self.assertIn('56', runner.state['runtime']['pending_recovery_reconciliation'])
        with Store(self.database) as store:
            store.upsert('klines', [candle('AUSDTM', NOW)])
        runner._reconcile_recovery(NOW + 2 * MINUTE)
        self.assertEqual(runner.state['runtime']['pending_recovery_reconciliation'], {})


if __name__ == '__main__':
    unittest.main()
