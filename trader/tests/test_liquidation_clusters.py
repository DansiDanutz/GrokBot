"""Implied liquidation clusters: synthetic series and a temporary database."""
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import unittest

from trader.data.liquidation_clusters import (
    HOUR_MS, METHOD, bin_width, build, contract_for, derive, main,
    radar_symbols, read_symbol, tier_weights,
)
from trader.data.store import Store

PRICE = 100.0
CONTRACT = dict(multiplier=1.0, tick_size=0.01, maintain_margin=0.005,
                max_leverage=50)
BURST_CONTRACTS = 1000.0
BURST_USD = BURST_CONTRACTS * PRICE
NOW_MS = 1_757_616_000_000


def level(leverage, side, price=PRICE, maintain_margin=0.005):
    """Binned liquidation price of one tier, at the module's own precision."""
    edge = 1 / leverage - maintain_margin
    return round(price * (1 - edge) if side == 'long' else price * (1 + edge), 9)


def series(*steps):
    """(hours, open_interest, price, funding) tuples into snapshot rows."""
    return [(NOW_MS + hours * HOUR_MS, interest, price, funding)
            for hours, interest, price, funding in steps]


class DeriveTests(unittest.TestCase):
    def test_single_burst_places_mass_at_every_allowed_tier(self):
        rows = series((0, 0.0, PRICE, 0.0), (1, BURST_CONTRACTS, PRICE, 0.0))
        entry = derive(rows, CONTRACT, NOW_MS + HOUR_MS)
        found = {(item['side'], round(item['price'], 9)): item['usd']
                 for item in entry['clusters']}
        expected = {}
        for leverage, weight in tier_weights(50).items():
            for side in ('long', 'short'):
                expected[(side, level(leverage, side))] = round(
                    BURST_USD * 0.5 * weight, 2)
        self.assertEqual(found, expected)
        self.assertEqual(entry['total_usd'], BURST_USD)
        self.assertEqual(entry['snapshots_used'], 2)
        self.assertEqual(entry['price'], PRICE)
        self.assertTrue(all(item['age_h'] == 0.0 for item in entry['clusters']))

    def test_positive_funding_tilts_mass_to_the_long_side(self):
        for funding, longs in ((0.0005, 0.6), (-0.0005, 0.4), (0.0, 0.5)):
            with self.subTest(funding=funding):
                rows = series((0, 0.0, PRICE, funding),
                              (1, BURST_CONTRACTS, PRICE, funding))
                entry = derive(rows, CONTRACT, NOW_MS + HOUR_MS)
                long_usd = sum(item['usd'] for item in entry['clusters']
                               if item['side'] == 'long')
                self.assertAlmostEqual(long_usd, BURST_USD * longs, places=6)

    def test_falling_open_interest_removes_mass_proportionally(self):
        rows = series((0, 0.0, PRICE, 0.0), (1, BURST_CONTRACTS, PRICE, 0.0),
                      (1, BURST_CONTRACTS / 2, PRICE, 0.0))
        opened = derive(rows[:2], CONTRACT, NOW_MS + HOUR_MS)
        closed = derive(rows[:2] + [(rows[1][0] + 1, BURST_CONTRACTS / 2,
                                     PRICE, 0.0)], CONTRACT, NOW_MS + HOUR_MS)
        self.assertAlmostEqual(closed['total_usd'], opened['total_usd'] / 2,
                               delta=1.0)
        shares_before = {(item['side'], item['price']): item['usd'] / opened['total_usd']
                         for item in opened['clusters']}
        shares_after = {(item['side'], item['price']): item['usd'] / closed['total_usd']
                        for item in closed['clusters']}
        for key, share in shares_before.items():
            self.assertAlmostEqual(shares_after[key], share, places=6)

    def test_two_day_old_mass_is_halved_and_reports_its_age(self):
        rows = series((0, 0.0, PRICE, 0.0), (1, BURST_CONTRACTS, PRICE, 0.0))
        entry = derive(rows, CONTRACT, NOW_MS + 49 * HOUR_MS)
        self.assertAlmostEqual(entry['total_usd'], BURST_USD / 2, delta=1.0)
        self.assertEqual({item['age_h'] for item in entry['clusters']}, {48.0})

    def test_nearest_levels_sit_on_the_tightest_tier_of_each_side(self):
        rows = series((0, 0.0, PRICE, 0.0), (1, BURST_CONTRACTS, PRICE, 0.0))
        entry = derive(rows, CONTRACT, NOW_MS + HOUR_MS)
        self.assertEqual(round(entry['nearest_below']['price'], 9), level(50, 'long'))
        self.assertEqual(entry['nearest_below']['side'], 'long')
        self.assertAlmostEqual(entry['nearest_below']['distance_pct'], 1.5)
        self.assertEqual(round(entry['nearest_above']['price'], 9), level(50, 'short'))
        self.assertEqual(entry['nearest_above']['side'], 'short')
        self.assertAlmostEqual(entry['nearest_above']['distance_pct'], 1.5)

    def test_nearest_above_is_null_once_price_clears_every_cluster(self):
        rows = series((0, 0.0, PRICE, 0.0), (1, BURST_CONTRACTS, PRICE, 0.0),
                      (2, BURST_CONTRACTS, 200.0, 0.0))
        entry = derive(rows, CONTRACT, NOW_MS + 2 * HOUR_MS)
        self.assertIsNone(entry['nearest_above'])
        self.assertEqual(round(entry['nearest_below']['price'], 9), level(5, 'short'))
        rows = series((0, 0.0, PRICE, 0.0), (1, BURST_CONTRACTS, PRICE, 0.0),
                      (2, BURST_CONTRACTS, 1.0, 0.0))
        entry = derive(rows, CONTRACT, NOW_MS + 2 * HOUR_MS)
        self.assertIsNone(entry['nearest_below'])
        self.assertEqual(round(entry['nearest_above']['price'], 9), level(5, 'long'))

    def test_bin_width_is_the_larger_of_tick_and_a_quarter_percent(self):
        self.assertEqual(bin_width(100.0, 0.01), 0.25)
        self.assertEqual(bin_width(100.0, 1.0), 1.0)
        self.assertEqual(bin_width(0.0, 0.01), 0.0)
        coarse = dict(CONTRACT, tick_size=5.0)
        rows = series((0, 0.0, PRICE, 0.0), (1, BURST_CONTRACTS, PRICE, 0.0))
        entry = derive(rows, coarse, NOW_MS + HOUR_MS)
        for item in entry['clusters']:
            self.assertAlmostEqual(item['price'] % 5.0, 0.0)
        self.assertEqual(entry['total_usd'], BURST_USD)

    def test_tiers_above_the_contract_maximum_are_dropped_and_renormalised(self):
        self.assertEqual(tier_weights(10), {5: 0.25 / 0.55, 10: 0.30 / 0.55})
        self.assertEqual(tier_weights(4), {})
        rows = series((0, 0.0, PRICE, 0.0), (1, BURST_CONTRACTS, PRICE, 0.0))
        entry = derive(rows, dict(CONTRACT, max_leverage=10), NOW_MS + HOUR_MS)
        self.assertEqual(sorted({round(item['price'], 9)
                                 for item in entry['clusters']}),
                         [level(5, 'long'), level(10, 'long'),
                          level(10, 'short'), level(5, 'short')])
        self.assertEqual(entry['total_usd'], BURST_USD)
        self.assertEqual(derive(rows, dict(CONTRACT, max_leverage=4),
                                NOW_MS + HOUR_MS)['clusters'], [])

    def test_unusable_rows_and_contracts_never_produce_infinite_numbers(self):
        rows = series((0, 0.0, PRICE, 0.0)) + [
            ('bad', 1.0, PRICE, 0.0), (NOW_MS + HOUR_MS, -1.0, PRICE, 0.0),
            (NOW_MS + HOUR_MS, 1.0, 0.0, 0.0), (NOW_MS + HOUR_MS, 1.0, PRICE,
                                                float('nan'))]
        entry = derive(rows, CONTRACT, NOW_MS + HOUR_MS)
        self.assertEqual((entry['snapshots_used'], entry['clusters']), (1, []))
        self.assertEqual(json.loads(json.dumps(entry, allow_nan=False)), entry)
        for broken in (dict(CONTRACT, multiplier=0), dict(CONTRACT, max_leverage=0),
                       dict(CONTRACT, maintain_margin=1.0),
                       dict(CONTRACT, tick_size=float('inf')), 'not a contract'):
            with self.subTest(broken=broken), self.assertRaises(ValueError):
                derive(rows, broken, NOW_MS + HOUR_MS)


def snapshots(store, symbol, rows, raw):
    """Write matching open_interest and ticker_snapshots ticks."""
    store.upsert('open_interest', [
        dict(symbol=symbol, time_ms=stamp, observed_at_ms=stamp,
             source_time_ms=stamp, open_interest=interest)
        for stamp, interest, _, _ in rows])
    store.upsert('ticker_snapshots', [
        dict(symbol=symbol, time_ms=stamp, observed_at_ms=stamp,
             source_time_ms=stamp, last=price, mark_price=price,
             index_price=price, volume_24h=1.0, turnover_24h=1.0,
             open_interest=interest, funding_rate=funding,
             raw_json=json.dumps(raw))
        for stamp, interest, price, funding in rows])


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.database = self.root / 'market.sqlite3'
        self.rows = series((0, 0.0, PRICE, 0.0),
                           (1, BURST_CONTRACTS, PRICE, 0.0))
        with Store(self.database) as store:
            snapshots(store, 'TESTUSDTM', self.rows,
                      dict(multiplier=1.0, tickSize=0.01, maintainMargin=0.005,
                           maxLeverage=50))
            snapshots(store, 'NOCONTRACTUSDTM', self.rows, dict(multiplier=1.0))

    def test_read_symbol_and_contract_for_use_the_stored_snapshots(self):
        with Store(self.database) as store:
            self.assertEqual(read_symbol(store.connection, 'TESTUSDTM',
                                         NOW_MS, NOW_MS + HOUR_MS), self.rows)
            self.assertEqual(read_symbol(store.connection, 'TESTUSDTM',
                                         NOW_MS + 1, NOW_MS + HOUR_MS),
                             self.rows[1:])
            self.assertEqual(contract_for(store.connection, 'TESTUSDTM'),
                             CONTRACT)
            for symbol in ('NOCONTRACTUSDTM', 'ABSENTUSDTM'):
                with self.subTest(symbol=symbol), self.assertRaises(ValueError):
                    contract_for(store.connection, symbol)

    def test_build_skips_unusable_symbols_and_stamps_the_method(self):
        report = build(self.database, ['TESTUSDTM', 'NOCONTRACTUSDTM',
                                       'ABSENTUSDTM', 'TESTUSDTM'],
                       NOW_MS + HOUR_MS)
        self.assertEqual(report['schema_version'], 1)
        self.assertEqual(report['method'], METHOD)
        self.assertEqual(report['window_hours'], 168)
        self.assertEqual(report['generated_at_ms'], NOW_MS + HOUR_MS)
        self.assertEqual(list(report['symbols']), ['TESTUSDTM'])
        self.assertEqual(report['symbols']['TESTUSDTM']['total_usd'], BURST_USD)

    def test_cli_writes_a_readable_file_atomically_with_one_status_line(self):
        out = self.root / 'clusters' / 'liquidation-clusters.json'
        printed = []
        code = main(['--database', str(self.database), '--out', str(out),
                     '--symbols', 'TESTUSDTM,ABSENTUSDTM',
                     '--now-ms', str(NOW_MS + HOUR_MS)], printer=printed.append)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(printed[0]),
                         dict(status='warn', symbols_ok=1, symbols_requested=2,
                              generated_at_ms=NOW_MS + HOUR_MS))
        self.assertEqual(os.stat(out).st_mode & 0o777, 0o644)
        self.assertEqual([path.name for path in out.parent.iterdir()],
                         [out.name])
        self.assertEqual(json.loads(out.read_text())['symbols']['TESTUSDTM'],
                         build(self.database, ['TESTUSDTM'],
                               NOW_MS + HOUR_MS)['symbols']['TESTUSDTM'])

    def test_cli_reports_failure_without_writing_nonsense(self):
        out = self.root / 'empty.json'
        printed = []
        code = main(['--database', str(self.database), '--out', str(out),
                     '--symbols', 'ABSENTUSDTM',
                     '--now-ms', str(NOW_MS)], printer=printed.append)
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(printed[0])['status'], 'fail')
        self.assertEqual(json.loads(out.read_text())['symbols'], {})

    def test_cli_refuses_a_missing_database_without_a_traceback(self):
        code = main(['--database', str(self.root / 'absent.sqlite3'),
                     '--out', str(self.root / 'out.json'),
                     '--symbols', 'TESTUSDTM'], printer=self.fail)
        self.assertEqual(code, 1)

    def test_from_radar_takes_every_row_symbol_in_order_up_to_the_limit(self):
        radar = self.root / 'radar.json'
        radar.write_text(json.dumps(dict(rows=[
            dict(symbol='TESTUSDTM'), dict(symbol='BUSDTM'),
            dict(symbol='TESTUSDTM'), dict(symbol='CUSDTM')])))
        self.assertEqual(radar_symbols(radar, 60),
                         ['TESTUSDTM', 'BUSDTM', 'CUSDTM'])
        self.assertEqual(radar_symbols(radar, 2), ['TESTUSDTM', 'BUSDTM'])
        out = self.root / 'from-radar.json'
        printed = []
        self.assertEqual(main(['--database', str(self.database), '--out',
                               str(out), '--from-radar', str(radar),
                               '--limit', '1', '--now-ms',
                               str(NOW_MS + HOUR_MS)], printer=printed.append), 0)
        self.assertEqual(json.loads(printed[0])['symbols_requested'], 1)
        broken = self.root / 'broken.json'
        broken.write_text(json.dumps(dict(rows='not a list')))
        with self.assertRaises(ValueError):
            radar_symbols(broken, 60)


class LaunchdTests(unittest.TestCase):
    def test_module_entrypoint_invokes_cli(self):
        result = subprocess.run(
            [sys.executable, '-m', 'trader.data.liquidation_clusters', '--help'],
            cwd=Path(__file__).parents[2], capture_output=True, text=True,
            check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--from-radar', result.stdout)

    def test_template_derives_hourly_before_the_radar_reads_it(self):
        path = (Path(__file__).parents[2] / 'config/launchd' /
                'com.danslab.trader-liq-clusters.plist.example')
        config = plistlib.loads(path.read_bytes())
        arguments = config['ProgramArguments']
        self.assertEqual(config['Label'], 'com.danslab.trader-liq-clusters')
        self.assertEqual(config['StartCalendarInterval'], {'Minute': 3})
        self.assertFalse(config['RunAtLoad'])
        self.assertEqual(config['ProcessType'], 'Background')
        self.assertEqual(config['WorkingDirectory'],
                         '/Users/davidai/ZCodeProject/GrokBot-prod')
        self.assertIn('trader.data.liquidation_clusters', arguments)
        self.assertIn('/Users/davidai/Sandbox/grokbot/market-data/'
                      'phase-2-20260911/market.sqlite3', arguments)
        self.assertIn('/Users/davidai/Sandbox/grokbot/radar/radar.json',
                      arguments)
        self.assertIn('/Users/davidai/Sandbox/grokbot/market-data/'
                      'liquidation-clusters.json', arguments)
        self.assertEqual(config['StandardOutPath'],
                         '/Users/davidai/Sandbox/grokbot/market-data/logs/'
                         'liq-clusters.out.log')
        self.assertNotIn('EnvironmentVariables', config)


if __name__ == '__main__':
    unittest.main()
