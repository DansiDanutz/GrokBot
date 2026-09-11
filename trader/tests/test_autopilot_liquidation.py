"""Offline liquidation estimates never mutate positions or allocate reserves."""
import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from trader.autopilot.liquidation import enrich, estimate

NOW = 10_000_000


def bot(q=50):
    return dict(symbol='RAYUSDTM', position_contracts=q, avg_entry=100,
                notional_usdt=1000, realized_pnl=10, fees_paid=6,
                funding_paid=4, reserve_usdt=200)


def metadata():
    return dict(symbol='RAYUSDTM', maintainMargin=0.01, minRiskLimit=10000)


class LiquidationTests(unittest.TestCase):
    def test_long_short_equity_equals_maintenance_at_estimate(self):
        for q in (50, -50):
            b = bot(q)
            r = estimate(b, metadata(), NOW, NOW)
            self.assertEqual(r['status'], 'ESTIMATED')
            p = r['price']
            self.assertAlmostEqual(1000 + q * (p - 100), abs(q) * p * .0106)
            self.assertAlmostEqual(1200 + q * (r['with_reserve_price'] - 100),
                                   abs(q) * r['with_reserve_price'] * .0106)
            self.assertEqual(r['fee_rate'], .0006)

    def test_flat_and_fully_collateralized_long(self):
        self.assertEqual(estimate(bot(0), metadata(), NOW, NOW)['status'], 'FLAT')
        self.assertEqual(estimate(bot(5), metadata(), NOW, NOW)['status'], 'NO_POSITIVE_PRICE')

    def test_freshness_and_tier_checked_at_entry_and_liquidation(self):
        self.assertEqual(estimate(bot(), metadata(), NOW - 7_200_001, NOW)['status'], 'STALE_METADATA')
        self.assertEqual(estimate(bot(), metadata(), NOW - 7_200_000, NOW)['status'], 'ESTIMATED')
        self.assertEqual(estimate(bot(), metadata(), NOW + 1, NOW)['status'], 'INVALID_METADATA')
        for q, cap in ((50, 4999), (-50, 5500)):
            m = dict(metadata(), minRiskLimit=cap)
            self.assertEqual(estimate(bot(q), m, NOW, NOW)['status'], 'TIER_UNAVAILABLE')

    def test_invalid_metadata_and_numbers_fail_closed(self):
        for change in ({'maintainMargin': float('nan')}, {'maintainMargin': True},
                       {'minRiskLimit': 0}, {'symbol': 'OTHER'}, {'maintainMargin': -1}):
            self.assertEqual(estimate(bot(), dict(metadata(), **change), NOW, NOW)['status'], 'INVALID_METADATA')
        self.assertEqual(estimate(dict(bot(), fees_paid=float('inf')), metadata(), NOW, NOW)['status'], 'INVALID_POSITION')

    def test_read_only_latest_per_coin_and_group_enrichment(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td).resolve() / 'market.sqlite3'
            with sqlite3.connect(path) as c:
                c.execute('CREATE TABLE ticker_snapshots(symbol TEXT, time_ms INTEGER, observed_at_ms INTEGER, raw_json TEXT)')
                c.execute('INSERT INTO ticker_snapshots VALUES(?,?,?,?)', ('RAYUSDTM', NOW-1, NOW-1, json.dumps(metadata())))
                c.execute('INSERT INTO ticker_snapshots VALUES(?,?,?,?)', ('RAYUSDTM', NOW, NOW, json.dumps(dict(metadata(), maintainMargin=.02))))
            source = dict(open_bots=[bot()], groups={'LONG': {'open_bots': [bot()]}})
            before = copy.deepcopy(source)
            result = enrich(source, path, NOW)
            self.assertEqual(result['open_bots'][0]['liquidation']['mmr'], .02)
            self.assertEqual(result['groups']['LONG']['open_bots'][0]['liquidation']['mmr'], .02)
            self.assertEqual(source, before)
            with sqlite3.connect(path) as c:
                c.execute('INSERT INTO ticker_snapshots VALUES(?,?,?,?)',
                          ('RAYUSDTM', NOW + 1, NOW + 1, json.dumps(metadata())))
            self.assertEqual(enrich(source, path, NOW)['open_bots'][0]['liquidation']['status'], 'INVALID_METADATA')
            self.assertEqual(estimate(bot(), {}, NOW, NOW)['status'], 'INVALID_METADATA')
            link = Path(td).resolve() / 'link.sqlite3'
            link.symlink_to(path)
            self.assertEqual(enrich(source, link, NOW)['open_bots'][0]['liquidation']['status'], 'METADATA_UNAVAILABLE')
            self.assertFalse((Path(td).resolve() / 'absent.sqlite3').exists())
            enrich(source, Path(td).resolve() / 'absent.sqlite3', NOW)
            self.assertFalse((Path(td).resolve() / 'absent.sqlite3').exists())

    def test_oversized_raw_metadata_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td).resolve() / 'db'
            with sqlite3.connect(path) as c:
                c.execute('CREATE TABLE ticker_snapshots(symbol TEXT, time_ms INTEGER, observed_at_ms INTEGER, raw_json TEXT)')
                c.execute('INSERT INTO ticker_snapshots VALUES(?,?,?,?)', ('RAYUSDTM', NOW, NOW, ' ' * 70000))
            self.assertEqual(enrich({'open_bots': [bot()]}, path, NOW)['open_bots'][0]['liquidation']['status'], 'INVALID_METADATA')
