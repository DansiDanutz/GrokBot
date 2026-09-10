import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from paper_grid import cli, engine

NOW = 1789084800.0


def quotes(now, price=10.0):
    return {'AAAUSDTM': dict(symbol='AAAUSDTM', bid=price, ask=price,
            mark=price, quote_time=now, lot_size=1, multiplier=1,
            tick_size=.01, funding_rate=.0001, funding_interval_hours=8,
            score=80, eligible=True, add_eligible=True, atr_pct=3,
            entry_edge_pct=5, turnover24h=1e7, bid_size=100000, ask_size=100000)}


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def collect(self, previous, held, now, force_scan=False):
        return quotes(now), {'top5': ['AAAUSDTM'], 'errors': {}, 'day': '2026-09-11'}

    def test_persist_restart_and_same_time_do_not_double_open(self):
        first = cli.run_cycle(self.root, collector=self.collect, now=NOW)
        with patch.object(cli.market, 'collect', side_effect=AssertionError('replayed fetch')):
            repeated = cli.run_cycle(self.root, now=NOW)
        self.assertEqual(first['cash'], repeated['cash'])
        self.assertEqual(len(repeated['positions']), 1)
        self.assertEqual(repeated['current_events'], [])
        restart = cli.run_cycle(self.root, collector=self.collect, now=NOW+60)
        self.assertEqual(len(restart['positions']), 1)
        self.assertEqual(len(list((self.root/'observations').glob('*.json'))), 2)

    def test_profit_close_survives_restart_without_second_credit(self):
        cli.run_cycle(self.root, collector=self.collect, now=NOW)
        def rose(previous, held, now, force_scan=False):
            return quotes(now, 10.5), {'top5':['AAAUSDTM'], 'errors':{}}
        closed = cli.run_cycle(self.root, collector=rose, now=NOW+60)
        repeated = cli.run_cycle(self.root, collector=rose, now=NOW+60)
        self.assertEqual(len(closed['positions']), 0)
        self.assertGreaterEqual(closed['realized_pnl'], 1)
        self.assertEqual(closed['cash'], repeated['cash'])

    def test_partial_write_failure_preserves_account(self):
        cli.run_cycle(self.root, collector=self.collect, now=NOW)
        original = (self.root/'account.json').read_bytes()
        with patch.object(cli.os, 'replace', side_effect=OSError('disk unavailable')):
            with self.assertRaises(OSError):
                cli.run_cycle(self.root, collector=self.collect, now=NOW+60)
        self.assertEqual((self.root/'account.json').read_bytes(), original)

    def test_corrupt_or_changed_config_is_not_reset(self):
        cli.run_cycle(self.root, collector=self.collect, now=NOW)
        path = self.root/'account.json'
        data = json.loads(path.read_text())
        data['config_hash'] = 'wrong'
        path.write_text(json.dumps(data))
        with self.assertRaises(ValueError):
            cli.run_cycle(self.root, collector=self.collect, now=NOW+60)
        self.assertEqual(json.loads(path.read_text())['config_hash'], 'wrong')
        path.write_text('{broken')
        with self.assertRaises(ValueError):
            cli.run_cycle(self.root, collector=self.collect, now=NOW+60)

    def test_concurrent_cycle_does_not_fetch_or_mutate(self):
        with cli.locked(self.root):
            with self.assertRaises(RuntimeError):
                cli.run_cycle(self.root, collector=self.collect, now=NOW)
        self.assertFalse((self.root/'account.json').exists())

    def test_pause_blocks_collection_and_preserves_balance(self):
        first=cli.run_cycle(self.root, collector=self.collect, now=NOW)
        envelope=cli.load(self.root, engine.default_config())
        envelope['paused']=True
        cli.atomic_json(self.root/'account.json', envelope)
        def forbidden(*args, **kwargs): raise AssertionError('must not collect')
        paused=cli.run_cycle(self.root, collector=forbidden, now=NOW+60)
        self.assertTrue(paused['paused'])
        self.assertEqual(paused['cash'], first['cash'])

    def test_api_outage_does_not_discard_position(self):
        first=cli.run_cycle(self.root, collector=self.collect, now=NOW)
        missing=lambda *a, **k: ({}, {'top5':['AAAUSDTM'], 'errors':{'global':'unavailable'}})
        failed=cli.run_cycle(self.root, collector=missing, now=NOW+120)
        self.assertEqual(set(first['positions']), set(failed['positions']))
        self.assertTrue(failed['equity_is_estimate'])
        self.assertEqual(failed['current_events'], [])
        self.assertEqual(failed['last_success_at'], NOW)


if __name__ == '__main__':
    unittest.main()
