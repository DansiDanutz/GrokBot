"""Offline contracts for the opportunity-cost backtest (synthetic ledger only)."""
import json
import sqlite3
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from trader.autopilot import policy
from trader.review import backtest_opportunity as backtest
from trader.tests.test_autopilot_policy import radar, row

HOUR = 3_600_000
MINUTE = 60_000


class BacktestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def _closed_state(self, reason='LABEL_FLIP'):
        """One bot opened at t0 and closed by a radar flip one hour later."""
        state, _ = policy.decide(policy.new_state(0), radar(long=[row('AUSDTM')]), {}, 0, 'a')
        with unittest.mock.patch.object(policy, 'OPPORTUNITY_COST_CLOSE', False):
            closed, _ = policy.decide(state, radar(short=[row('AUSDTM', 'SHORT')]),
                                      {}, HOUR, 'b')
        self.assertEqual(closed['closed_bots'][0]['engine']['reason'], reason)
        path = self.root / 'state.json'
        path.write_text(json.dumps(closed))
        return path

    def _events(self, opens=()):
        path = self.root / 'events.jsonl'
        rows = [dict(ts_ms=0, bot_id=1, symbol='AUSDTM', type='OPEN', price=100, equity=1200)]
        rows += [dict(ts_ms=ts, bot_id=2, symbol=symbol, type='OPEN', price=100, equity=1200)
                 for ts, symbol in opens]
        path.write_text('\n'.join(json.dumps(row) for row in rows) + '\n')
        return self.root

    def _market(self, minutes=500, price=100.0):
        path = self.root / 'market.sqlite3'
        connection = sqlite3.connect(path)
        with connection:
            connection.execute('CREATE TABLE klines (symbol TEXT, interval TEXT, '
                               'time_ms INTEGER, open REAL, high REAL, low REAL, close REAL)')
            connection.executemany(
                'INSERT INTO klines VALUES (?,?,?,?,?,?,?)',
                [('AUSDTM', '1m', i * MINUTE, price, price + .5, price - .5, price)
                 for i in range(minutes)])
        connection.close()
        return path

    def test_reports_a_held_close_when_no_replacement_opened(self):
        results, text = backtest.run(self._closed_state(), self._events(),
                                     self._market(), hours=6)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['replacements'], [])
        self.assertEqual(results[0]['reason'], 'LABEL_FLIP')
        self.assertIsNotNone(results[0]['replay_at_close'])
        self.assertIn('1 of 1 LABEL_FLIP closes', text)
        self.assertIn('anecdote', text)

    def test_a_replacement_in_the_next_pass_counts_as_evidence(self):
        events = self._events(opens=[(HOUR + 120_000, 'BUSDTM')])
        results, text = backtest.run(self._closed_state(), events, self._market(), hours=6)
        self.assertEqual(results[0]['replacements'], ['BUSDTM'])
        self.assertIn('0 of 1 LABEL_FLIP closes', text)

    def test_an_open_beyond_the_next_pass_is_not_a_replacement(self):
        events = self._events(opens=[(HOUR + 2 * HOUR, 'BUSDTM')])
        results, _ = backtest.run(self._closed_state(), events, self._market(), hours=6)
        self.assertEqual(results[0]['replacements'], [])

    def test_missing_klines_are_reported_not_guessed(self):
        results, text = backtest.run(self._closed_state(), self._events(),
                                     self._market(minutes=0), hours=6)
        self.assertEqual(results[0]['replay_error'], 'no 1m klines')
        self.assertIsNone(results[0].get('replay_at_close'))
        self.assertIn('replay unavailable', text)

    def test_the_live_market_database_is_opened_read_only(self):
        path = self._market()
        rows = backtest.candles(path, 'AUSDTM', 0, 10 * MINUTE)
        self.assertEqual(len(rows), 11)
        self.assertEqual(set(rows[0]), {'ts_ms', 'open', 'high', 'low', 'close'})
        connection = sqlite3.connect(f'file:{path}?mode=ro&immutable=1', uri=True)
        with self.assertRaises(sqlite3.OperationalError):
            connection.execute("INSERT INTO klines VALUES ('X','1m',0,1,1,1,1)")
        connection.close()

    def test_risk_closes_are_not_examined(self):
        state, _ = policy.decide(policy.new_state(0), radar(long=[row('AUSDTM')]), {}, 0, 'a')
        broken, _ = policy.advance(state, {'AUSDTM': {'ts_ms': MINUTE, 'price': 140}})
        path = self.root / 'state.json'
        path.write_text(json.dumps(broken))
        results, text = backtest.run(path, self._events(), self._market(), hours=6)
        self.assertEqual(results, [])
        self.assertIn('Non-risk closes examined: 0', text)


if __name__ == '__main__':
    unittest.main()
