"""Offline end-to-end run of the hedge backtest against a synthetic tape."""
import json
import math
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from trader.review import backtest_hedge as backtest

MINUTE = 60_000
SPAN = 60


def _price(index):
    """Down for half an hour, then back up: one hedge decision worth making."""
    return 1.5 - .01 * index if index <= 30 else 1.2 + .01 * (index - 30)


def _database(path, symbol):
    with closing(sqlite3.connect(path)) as db, db:
        db.execute('CREATE TABLE klines (symbol TEXT, interval TEXT, time_ms INTEGER,'
                   ' open REAL, high REAL, low REAL, close REAL)')
        db.execute('CREATE TABLE ticker_snapshots (symbol TEXT, time_ms INTEGER,'
                   ' observed_at_ms INTEGER, mark_price REAL, funding_rate REAL,'
                   ' raw_json TEXT)')
        db.execute('CREATE TABLE open_interest (symbol TEXT, time_ms INTEGER,'
                   ' open_interest REAL)')
        raw = json.dumps(dict(multiplier=1, tickSize=.0001, maintainMargin=.005,
                              maxLeverage=20))
        for index in range(SPAN):
            low, high = min(_price(index), _price(index + 1)), max(_price(index), _price(index + 1))
            db.execute('INSERT INTO klines VALUES (?,?,?,?,?,?,?)',
                       (symbol, '1m', index * MINUTE, _price(index), high, low,
                        _price(index + 1)))
            db.execute('INSERT INTO ticker_snapshots VALUES (?,?,?,?,?,?)',
                       (symbol, index * MINUTE, index * MINUTE, _price(index), .0001, raw))
            db.execute('INSERT INTO open_interest VALUES (?,?,?)',
                       (symbol, index * MINUTE, 1000 + 10 * index))


def _state(symbol):
    engine = dict(bot_id=1, symbol=symbol, direction='LONG', range_low=1.0,
                  range_high=2.0, grid_interval=.05, grids=20, step_pct=.5,
                  contracts_per_line=10.0, notional_usdt=1000, leverage=5,
                  funding_pct=.01, accounting_version=2, maintain_margin=.005,
                  risk_limit=1_000_000, fee_rate_maker=.0006, fee_rate_taker=.0006,
                  opening_price=1.5, opened_ms=0, closed_ms=SPAN * MINUTE,
                  reason='RANGE_BREAK', realized_pnl=-5.0, unrealized_pnl=-20.0,
                  fees_paid=1.0, funding_paid=.5, grid_profit=3.0,
                  completed_grids=4, lines=[], empty_line=0)
    return dict(closed_bots=[dict(engine=engine)], open_bots=[])


class BacktestHedgeTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        root = Path(self.dir.name)
        self.symbol = 'ZUSDTM'
        self.database = str(root / 'market.sqlite3')
        self.state = str(root / 'state.json')
        _database(self.database, self.symbol)
        Path(self.state).write_text(json.dumps(_state(self.symbol)))
        self.cases, self.rows = backtest.run(self.state, self.database)

    def tearDown(self):
        self.dir.cleanup()

    def test_replay_reconstructs_the_bot_and_sweeps_the_whole_family(self):
        case = self.cases[0]
        self.assertEqual(case['candles'], SPAN)
        self.assertGreater(case['replay_grids'], 0)
        self.assertEqual(len(self.rows),
                         len(backtest.RATIOS) * len(backtest.FRACTIONS)
                         * len(backtest.DISTANCES))
        self.assertTrue(all(math.isfinite(row['total']) for row in self.rows))

    def test_marks_are_quarter_hourly_and_carry_point_in_time_clusters(self):
        marks = self.cases[0]['marks']
        self.assertTrue(marks)
        self.assertTrue(all(mark['ts_ms'] % backtest.MARK_MS == 0 for mark in marks))
        self.assertEqual([mark['ts_ms'] for mark in marks],
                         sorted(mark['ts_ms'] for mark in marks))
        for mark in marks:
            self.assertEqual(mark['clusters']['liq_clusters_generated_at_ms'],
                             mark['ts_ms'])

    def test_a_triggered_rule_changes_the_net_and_an_untriggered_one_does_not(self):
        case = self.cases[0]
        fired = backtest.rule_deltas(self.cases, 2.0, .5, None)
        self.assertEqual(len(fired), 1)
        self.assertNotAlmostEqual(fired[0][3], 0.0)
        # An unreachable position fraction can never fire.
        self.assertEqual(backtest.rule_deltas(self.cases, 2.0, 99.0, None), [])
        self.assertIsNone(backtest.first_trigger(case, 2.0, 99.0, None))

    def test_report_renders_and_names_the_baseline_and_the_rules(self):
        body, deltas = backtest.detail(self.cases, max(
            self.rows, key=lambda row: row['total']))
        self.assertIn(self.symbol, body)
        self.assertIn('no-op baseline: 1 bots', backtest.table(self.rows, self.cases))
        self.assertIn('drop the single best bot', backtest.robustness(deltas))
        self.assertIn('nothing to be robust about', backtest.robustness([]))
        self.assertIn(self.symbol, backtest.fidelity(self.cases))

    def test_main_prints_a_report_without_touching_any_state(self):
        printed = []
        before = Path(self.state).read_bytes()
        code = backtest.main(['--state', self.state, '--database', self.database],
                             printer=printed.append)
        self.assertEqual(code, 0)
        self.assertIn('no-op baseline', '\n'.join(printed))
        self.assertEqual(Path(self.state).read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
