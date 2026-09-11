"""Isolated deadline, accounting and publication guarantees; no API requests."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from paper_grid import cli, engine, experiment
from paper_grid.test_cli import NOW, quotes


class ExperimentTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        config = engine.default_config()
        account = dict(schema=1, config=config, config_hash=cli.config_fingerprint(config),
                       state=engine.initial_state(config, NOW-1000), paused=True,
                       scan={'top5': ['AAAUSDTM']}, market={}, events=[])
        cli.atomic_json(self.root/'account.json', account)

    def collect(self, previous, held, now, force_scan=False):
        return quotes(now), {'top5': ['AAAUSDTM'], 'errors': {}}

    def features(self, symbols, now, cache=None):
        return dict(fetched_at=now, symbols={s: dict(eligible=True, latest_hour=(int(now)//3600-1)*3600,
                    total_usd=3000, long_share=.8, burst_ratio=4, reason='pass') for s in symbols}, errors=[])

    def begin(self, **kwargs):
        return experiment.start(self.root, now=NOW, **kwargs)

    def tick(self, now=NOW, **kwargs):
        return experiment.tick(self.root, now=now, collector=kwargs.pop('collector', self.collect),
                               feature_collector=kwargs.pop('feature_collector', self.features), **kwargs)

    def doc(self):
        return json.loads((self.root/experiment.FILE).read_text())

    def test_start_clones_existing_account_and_never_resets(self):
        source = json.loads((self.root/'account.json').read_text())
        source['state']['cash'] = 900
        source['state']['realized_pnl'] = -100
        cli.atomic_json(self.root/'account.json', source)
        self.begin()
        for arm in self.doc()['accounts'].values():
            self.assertEqual(arm['state'], source['state'])
            self.assertEqual(arm['statistics']['initial_equity'], 900)
        self.assertEqual(json.loads((self.root/'account.json').read_text()), source)
        with self.assertRaises(ValueError): self.begin()

    def test_missing_source_never_creates_balance(self):
        (self.root/'account.json').unlink()
        with self.assertRaises(ValueError): self.begin()
        self.assertFalse((self.root/experiment.FILE).exists())

    def test_continuation_preserves_accounts_and_trades_after_old_deadline(self):
        self.begin()
        self.tick()
        before = self.doc()
        result = experiment.continue_running(self.root, now=NOW+60)
        self.assertTrue(result['experiment']['continuous'])
        self.assertIsNone(result['experiment']['end_at'])
        self.assertEqual(self.doc()['accounts'], before['accounts'])
        self.assertEqual(self.doc()['events'], before['events'])
        self.assertEqual(self.doc()['observations'], before['observations'])
        self.assertEqual(result['experiment']['next_audit_at'], NOW+48*3600)
        self.tick(NOW+48*3600+1)
        self.assertEqual(self.doc()['status'], 'running')
        self.assertEqual(self.doc()['last_tick_at'], NOW+48*3600+1)

    def test_continuation_is_idempotent_and_does_not_override_manual_freeze(self):
        self.begin()
        experiment.continue_running(self.root, now=NOW+1)
        first = (self.root/experiment.FILE).read_bytes()
        experiment.continue_running(self.root, now=NOW+2)
        self.assertEqual(first, (self.root/experiment.FILE).read_bytes())
        experiment.freeze(self.root, now=NOW+3)
        with self.assertRaises(ValueError): experiment.continue_running(self.root, now=NOW+4)

    def test_continuous_still_enforces_code_integrity(self):
        self.begin()
        experiment.continue_running(self.root, now=NOW+1)
        with patch.object(experiment, '_code_hashes', return_value={'changed': 'yes'}):
            result = self.tick(NOW+300)
        self.assertEqual(result['experiment']['status'], 'frozen')
        self.assertEqual(result['experiment']['freeze_reason'], 'code_changed')

    def test_deadline_before_fetch_and_before_start(self):
        self.begin()
        forbidden = lambda *a, **k: self.fail('API must not run')
        self.tick(NOW-1, collector=forbidden, feature_collector=forbidden)
        frozen = self.tick(NOW+48*3600, collector=forbidden, feature_collector=forbidden)
        self.assertEqual(frozen['experiment']['status'], 'completed')
        self.assertIsNone(frozen['experiment']['last_tick_at'])

    def test_deadline_after_features_does_not_fetch_market(self):
        self.begin(hours=1)
        clock = [NOW+3599]
        def features(*args, **kwargs):
            clock[0] = NOW+3601
            return self.features(['AAAUSDTM'], NOW+3599)
        with patch.object(experiment.time, 'time', side_effect=lambda: clock[0]):
            result = experiment.tick(self.root, feature_collector=features,
                     collector=lambda *a, **k: self.fail('late market fetch'))
        self.assertEqual(result['experiment']['status'], 'completed')
        self.assertFalse(self.doc()['accounts']['baseline']['state']['positions'])

    def test_deadline_after_market_does_not_fill(self):
        self.begin(hours=1)
        clock = [NOW+3599]
        def collect(*args, **kwargs):
            clock[0] = NOW+3601
            return self.collect(None, [], NOW+3599)
        with patch.object(experiment.time, 'time', side_effect=lambda: clock[0]):
            result = experiment.tick(self.root, feature_collector=self.features, collector=collect)
        self.assertEqual(result['experiment']['status'], 'completed')
        self.assertFalse(self.doc()['accounts']['baseline']['state']['positions'])
        self.assertEqual(self.doc()['observations'][-1]['skipped'], 'deadline')

    def test_deadline_during_steps_rolls_back_both_arms(self):
        self.begin(hours=1)
        clock = [NOW+3599]
        original = experiment._advance
        def advance(*args):
            result = original(*args)
            clock[0] = NOW+3601
            return result
        with patch.object(experiment.time, 'time', side_effect=lambda: clock[0]), patch.object(experiment, '_advance', side_effect=advance):
            experiment.tick(self.root, feature_collector=self.features, collector=self.collect)
        self.assertEqual(self.doc()['status'], 'completed')
        for arm in self.doc()['accounts'].values(): self.assertFalse(arm['state']['positions'])

    def test_both_arms_identical_when_filter_passes(self):
        self.begin()
        self.tick()
        arms = self.doc()['accounts']
        self.assertEqual(arms['baseline']['state'], arms['liquidation_filter']['state'])
        self.assertTrue(arms['baseline']['state']['positions'])
        self.assertEqual(len(self.doc()['observations']), 1)

    def test_duplicate_and_subinterval_do_not_fetch_or_settle(self):
        self.begin()
        self.tick()
        original = (self.root/experiment.FILE).read_bytes()
        forbidden = lambda *a, **k: self.fail('duplicate fetch')
        for now in (NOW, NOW+299, NOW-1): self.tick(now, collector=forbidden, feature_collector=forbidden)
        self.assertEqual((self.root/experiment.FILE).read_bytes(), original)

    def test_one_lock_rejects_simultaneous_worker(self):
        self.begin()
        with cli.locked(self.root), self.assertRaises(RuntimeError): self.tick()
        self.assertIsNone(self.doc()['last_tick_at'])

    def test_atomic_commit_failure_keeps_both_original_accounts(self):
        self.begin()
        original = (self.root/experiment.FILE).read_bytes()
        with patch.object(cli.os, 'replace', side_effect=OSError('disk full')):
            with self.assertRaises(OSError): self.tick()
        self.assertEqual((self.root/experiment.FILE).read_bytes(), original)

    def test_second_arm_failure_does_not_settle_first_arm(self):
        self.begin()
        original = deepcopy(self.doc()['accounts'])
        advance = experiment._advance
        calls = []
        def fail_second(*args):
            calls.append(1)
            if len(calls) == 2: raise ValueError('failure')
            return advance(*args)
        with patch.object(experiment, '_advance', side_effect=fail_second): self.tick()
        self.assertEqual(self.doc()['accounts'], original)
        self.assertIsNone(self.doc()['last_tick_at'])
        self.assertEqual(self.doc()['errors'][-1]['type'], 'ValueError')

    def test_corrupt_state_and_changed_config_refuse_reset(self):
        self.begin()
        path = self.root/experiment.FILE
        original = self.doc()
        for damage in ('hash', 'config', 'state', 'nan', 'json'):
            doc = deepcopy(original)
            if damage == 'hash': doc['config_hash'] = 'bad'
            if damage == 'config': doc['config']['leverage'] = 5
            if damage == 'state': doc['accounts']['baseline']['state']['cash'] = -1
            if damage == 'nan': doc['accounts']['baseline']['statistics']['total_fees'] = float('nan')
            path.write_text('{broken' if damage == 'json' else json.dumps(doc))
            before = path.read_bytes()
            with self.assertRaises(ValueError): self.tick()
            self.assertEqual(path.read_bytes(), before)

    def test_missing_coinglass_blocks_entries_but_not_exits(self):
        self.begin()
        self.tick()
        def rose(*args, **kwargs): return quotes(NOW+1800, 10.5), {'top5': ['AAAUSDTM']}
        missing = lambda *a, **k: dict(fetched_at=NOW+1800, symbols={}, errors=[])
        self.tick(NOW+1800, collector=rose, feature_collector=missing)
        for arm in self.doc()['accounts'].values():
            self.assertFalse(arm['state']['positions'])
            self.assertGreater(arm['state']['realized_pnl'], 1)
        self.tick(NOW+2100, collector=lambda *a, **k: (quotes(NOW+2100), {'top5':['AAAUSDTM']}), feature_collector=missing)
        self.assertTrue(self.doc()['accounts']['baseline']['state']['positions'])
        self.assertFalse(self.doc()['accounts']['liquidation_filter']['state']['positions'])

    def test_frozen_positions_and_final_report_never_change(self):
        self.begin()
        self.tick()
        before = deepcopy(self.doc()['accounts'])
        final = self.tick(NOW+48*3600)
        self.assertEqual(self.doc()['accounts'], before)
        self.assertTrue(final['accounts']['baseline']['equity_is_estimate'])
        self.assertTrue(final['accounts']['baseline']['positions'])
        frozen = (self.root/experiment.FILE).read_bytes()
        self.tick(NOW+50*3600)
        experiment.freeze(self.root, now=NOW+50*3600)
        read = experiment.report(self.root, now=NOW+70*3600)
        self.assertEqual(read['experiment']['report_at'], NOW+48*3600)
        self.assertEqual((self.root/experiment.FILE).read_bytes(), frozen)

    def test_report_publishes_every_half_hour_and_read_does_not_refresh(self):
        self.begin()
        self.tick()
        self.assertEqual(experiment.report(self.root, now=NOW+600)['experiment']['report_at'], NOW)
        result = self.tick(NOW+1800)
        self.assertEqual(result['experiment']['report_at'], NOW+1800)
        self.assertEqual(len(result['history']), 2)
        snapshot = (self.root/experiment.FILE).read_bytes()
        experiment.report(self.root, now=NOW+2000)
        self.assertEqual((self.root/experiment.FILE).read_bytes(), snapshot)

    def test_feature_cache_never_reused_beyond_half_hour(self):
        self.begin()
        calls = []
        def features(*args, **kwargs):
            calls.append(args[1])
            return self.features(*args, **kwargs)
        for now in (NOW, NOW+300, NOW+1799, NOW+2099): self.tick(now, feature_collector=features)
        self.assertEqual(calls, [NOW, NOW+2099])

    def test_changed_algorithm_freezes_without_fetch_and_report_stays_readable(self):
        self.begin()
        self.tick()
        before = deepcopy(self.doc()['accounts'])
        with patch.object(experiment, '_code_hashes', return_value={'engine.py': 'changed'}):
            result = self.tick(NOW+300, collector=lambda *a, **k: self.fail('code changed fetch'))
            self.assertEqual(experiment.report(self.root, NOW+300)['experiment']['status'], 'frozen')
        self.assertEqual(result['experiment']['freeze_reason'], 'code_changed')
        self.assertEqual(self.doc()['accounts'], before)

    def test_positions_show_net_pnl_and_estimated_stale_value(self):
        self.begin()
        self.tick()
        result = experiment.freeze(self.root, now=NOW+600)
        position = result['accounts']['baseline']['positions']['AAAUSDTM']
        self.assertLess(position['unrealized_net_pnl'], 0)
        self.assertTrue(position['equity_is_estimate'])
        self.assertAlmostEqual(position['unrealized_net_pnl'], result['accounts']['baseline']['unrealized_net_pnl'])

    def test_new_candidate_gets_features_same_cycle_and_both_arms_fresh_quotes(self):
        self.begin()
        clock = [NOW]
        market_calls, feature_calls = [], []
        def collect(previous, held, now, force_scan=False):
            market_calls.append(now)
            result = quotes(now)
            new = deepcopy(result['AAAUSDTM'])
            new['symbol'] = 'BBBUSDTM'
            result['BBBUSDTM'] = new
            return result, {'top5': ['AAAUSDTM', 'BBBUSDTM']}
        def features(symbols, now, cache=None):
            feature_calls.append(list(symbols))
            if 'BBBUSDTM' in symbols:
                clock[0] = NOW+120
            return self.features(symbols, now, cache)
        with patch.object(experiment.time, 'time', side_effect=lambda: clock[0]):
            experiment.tick(self.root, collector=collect, feature_collector=features)
        self.assertEqual(feature_calls, [['AAAUSDTM'], ['BBBUSDTM']])
        self.assertEqual(market_calls, [NOW, NOW+120])
        for arm in self.doc()['accounts'].values():
            self.assertEqual(set(arm['state']['positions']), {'AAAUSDTM', 'BBBUSDTM'})
            self.assertEqual(arm['market']['BBBUSDTM']['quote_time'], NOW+120)

    def test_deadline_during_new_symbol_features_prevents_market_refetch(self):
        self.begin(hours=1)
        clock = [NOW+3599]
        calls = []
        def collect(previous, held, now, force_scan=False):
            calls.append(now)
            result = quotes(now)
            result['BBBUSDTM'] = dict(result['AAAUSDTM'], symbol='BBBUSDTM')
            return result, {'top5': ['AAAUSDTM', 'BBBUSDTM']}
        def features(symbols, now, cache=None):
            if 'BBBUSDTM' in symbols: clock[0] = NOW+3601
            return self.features(symbols, now, cache)
        with patch.object(experiment.time, 'time', side_effect=lambda: clock[0]):
            result = experiment.tick(self.root, collector=collect, feature_collector=features)
        self.assertEqual(len(calls), 1)
        self.assertEqual(result['experiment']['status'], 'completed')
        self.assertFalse(self.doc()['accounts']['baseline']['state']['positions'])

    def test_first_success_publishes_immediately_then_waits_half_hour(self):
        self.begin()
        first = self.tick(NOW+60)
        self.assertEqual(first['experiment']['report_at'], NOW+60)
        self.assertTrue(first['coinglass']['symbols'])
        self.assertTrue(first['candidates'])
        self.assertTrue(first['accounts']['baseline']['positions'])
        early = self.tick(NOW+1800)
        self.assertEqual(early['experiment']['report_at'], NOW+60)
        published = self.tick(NOW+2100)
        self.assertEqual(published['experiment']['report_at'], NOW+2100)

    def test_equity_is_recorded_each_successful_tick_without_backfill(self):
        self.begin()
        self.tick()
        first = self.doc()['observations'][0]
        self.assertEqual(first['telemetry_schema'], 1)
        for arm in experiment.ARMS:
            account = self.doc()['accounts'][arm]
            expected = engine.status(account['state'], account['market'], NOW, account['config'])
            self.assertEqual(first['equity'][arm], {key: expected[key] for key in ('equity', 'equity_is_estimate')})
        # Historical observations are intentionally incomplete, never reconstructed.
        doc = self.doc()
        doc['observations'][0].pop('equity')
        doc['observations'][0].pop('telemetry_schema')
        cli.atomic_json(self.root/experiment.FILE, doc)
        self.tick(NOW+300, collector=lambda *a, **k: ({}, {'top5': []}))
        rows = self.doc()['observations']
        self.assertNotIn('equity', rows[0])
        self.assertTrue(rows[-1]['equity']['baseline']['equity_is_estimate'])
        self.assertEqual(len(self.doc()['history']), 1)
        self.assertEqual(self.doc()['telemetry_schema'], 1)

    def test_union_held_passed_to_single_market_fetch(self):
        self.begin()
        self.tick()
        doc = self.doc()
        b = doc['accounts']['liquidation_filter']['state']['positions']
        b['BBBUSDTM'] = b.pop('AAAUSDTM')
        doc['coinglass']['symbols']['BBBUSDTM'] = deepcopy(doc['coinglass']['symbols']['AAAUSDTM'])
        cli.atomic_json(self.root/experiment.FILE, doc)
        calls = []
        def collect(previous, held, now, force_scan=False):
            calls.append(held)
            return {}, {'top5': []}
        self.tick(NOW+300, collector=collect)
        self.assertEqual(calls, [['AAAUSDTM', 'BBBUSDTM']])


if __name__ == '__main__': unittest.main()
