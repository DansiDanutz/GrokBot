"""Offline tests for continuous audit scheduling and honest accounting."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from paper_grid import audits, cli, engine, retention


def timestamp(value):
    return datetime.fromisoformat(value).timestamp()


START = timestamp('2026-09-11T02:11:55+03:00')


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.doc = dict(mode='paper', start_at=START, config=engine.default_config(),
            tick_seconds=300, report_seconds=1800, last_tick_at=START, report_at=START,
            code_hashes={'engine.py':'sample'}, config_hash='fixture',
            accounts={name:dict(statistics={'initial_equity':1000}) for name in audits.ARMS},
            history=[dict(time=START, baseline_equity=1000, liquidation_filter_equity=1000)],
            events=[], errors=[], observations=[])
        self.save()

    def save(self):
        cli.atomic_json(self.root/'experiment.json', self.doc)

    def report(self, record):
        return json.loads((self.root/'audits'/(record['id']+'.json')).read_text())

    def generate(self, at=START+48*3600):
        self.save()
        return audits.generate_due(self.root, at)

    def observation(self, at, feature=True):
        features = dict(fetched_at=at, symbols={})
        if feature:
            features['symbols']['TESTUSDTM'] = dict(latest_hour=(int(at)//3600-1)*3600,
                burst_ratio=4, long_share=.8, total_usd=100, eligible=True)
        return dict(time=at, market={'TESTUSDTM':dict(quote_time=at, eligible=True)},
                    scan={'top5':['TESTUSDTM']}, coinglass=features)

    def test_due_windows_and_repeated_generation_are_idempotent(self):
        original = (self.root/'experiment.json').read_bytes()
        records = audits.generate_due(self.root, START+48*3600)
        self.assertEqual([r['kind'] for r in records], ['daily', 'daily', 'audit48h'])
        self.assertEqual(records[-1]['start_at'], START)
        self.assertEqual(records[-1]['end_at']-START, 48*3600)
        self.assertEqual(audits.generate_due(self.root, START+48*3600), [])
        self.assertEqual((self.root/'experiment.json').read_bytes(), original)
        self.assertEqual(len(audits.list_reports(self.root)), 3)
        for r in records:
            for suffix in ('html','md','json'):
                self.assertTrue((self.root/'audits'/(r['id']+'.'+suffix)).is_file())

    def test_generation_requests_only_due_observation_windows(self):
        with patch.object(audits.metric_evidence, 'load',
                          wraps=audits.metric_evidence.load) as loader:
            records = self.generate()
        expected = [(row['start_at'], row['end_at']) for row in records]
        self.assertEqual(loader.call_args.kwargs.get('windows'), expected)

    def test_daily_and_weekly_local_boundaries(self):
        records = self.generate(timestamp('2026-09-14T09:00:00+03:00'))
        weekly = next(r for r in records if r['kind']=='weekly')
        self.assertEqual(weekly['start_at'], START)
        self.assertEqual(weekly['end_at'], timestamp('2026-09-14T09:00:00+03:00'))
        for r in records:
            if r['kind']=='daily':
                self.assertEqual(datetime.fromtimestamp(r['end_at'], audits.ZONE).hour, 0)
        daily = [r for r in records if r['kind']=='daily']
        self.assertEqual(daily[1]['start_at'], daily[0]['end_at'])

    def test_dst_daily_is_calendar_time_and_audit_is_elapsed(self):
        before = timestamp('2026-03-29T00:00:00+02:00')
        after = audits._next('daily', before)
        self.assertEqual(after-before, 23*3600)
        self.assertEqual(audits._next('audit48h', before)-before, 48*3600)
        autumn = timestamp('2026-10-25T00:00:00+03:00')
        self.assertEqual(audits._next('daily', autumn)-autumn, 25*3600)
        monday = timestamp('2026-03-23T09:00:00+02:00')
        self.assertEqual(audits._next('weekly', monday), timestamp('2026-03-30T09:00:00+03:00'))

    def test_before_first_boundary_has_no_reports(self):
        self.assertEqual(self.generate(START+60), [])
        self.assertFalse((self.root/'audits'/'manifest.json').exists())

    def test_long_catchup_is_bounded_and_does_not_skip_windows(self):
        future = timestamp('2036-09-11T02:11:55+03:00')
        first = self.generate(future)
        second = audits.generate_due(self.root, future)
        self.assertEqual(len(first), 8)
        self.assertEqual(len(second), 8)
        self.assertGreaterEqual(second[0]['end_at'], first[-1]['end_at'])
        audit = [r for r in first+second if r['kind']=='audit48h']
        self.assertEqual(audit[0]['start_at'], START)
        self.assertEqual(audit[1]['start_at'], audit[0]['end_at'])
        self.assertEqual(self.report(first[0])['severity'], 'critical')
        self.assertEqual(self.report(first[0])['data']['cycle_coverage']['observed'], 0)

    def test_event_window_excludes_start_includes_end(self):
        end = START+48*3600
        for at, net in [(START,100), (START+1,-3), (end,4), (end+1,100)]:
            self.doc['events'].append(dict(time=at, account='baseline', type='close', net_pnl=net,
                exit_fee=.05, entry_fees=.06, funding_model_cost=.01, reason='test'))
        self.doc['events'].append(dict(time=START+2, account='baseline', type='open', fee=.1))
        self.doc['history'] += [dict(time=START+1800, baseline_equity=998, liquidation_filter_equity=1000),
                               dict(time=end, baseline_equity=1002, liquidation_filter_equity=999)]
        audit = self.report(next(r for r in self.generate(end) if r['kind']=='audit48h'))
        result = audit['accounts']['baseline']
        self.assertEqual(result['closed_trades'], 2)
        self.assertEqual(result['closed_trade_net_pnl'], 1)
        self.assertEqual(result['equity_change'], 2)
        self.assertEqual(result['non_realized_equity_change_residual'], 1)
        self.assertEqual(result['expectancy_net_per_close'], .5)
        self.assertEqual(result['wins'], 1)
        self.assertEqual(result['losses'], 1)
        self.assertAlmostEqual(result['fees_paid_during_window'], .2)
        self.assertAlmostEqual(result['lifetime_funding_on_window_closes'], .02)
        self.assertIsNone(result['period_funding_accrual'])
        self.assertEqual(result['observed_max_drawdown'], 2)
        self.assertEqual(result['cumulative']['closed_trade_net_since_start'], 1)
        self.assertEqual(audit['comparison']['filtered_minus_baseline_equity_change'], -3)

    def test_marks_never_look_ahead_and_disclose_staleness(self):
        end = START+48*3600
        self.doc['history'] += [dict(time=end-100, baseline_equity=990, liquidation_filter_equity=1000),
                               dict(time=end+1, baseline_equity=800, liquidation_filter_equity=800)]
        audit = self.report(next(r for r in self.generate(end) if r['kind']=='audit48h'))
        result = audit['accounts']['baseline']
        self.assertEqual(result['end_mark']['time'], end-100)
        self.assertEqual(result['equity_change'], -10)
        self.assertEqual(result['end_mark_age_seconds'], 100)

    def test_empty_window_reports_unknown_expectancy_and_drawdown(self):
        report = self.report(self.generate()[0])
        self.assertIsNone(report['accounts']['baseline']['expectancy_net_per_close'])
        self.assertIsNone(report['accounts']['baseline']['observed_max_drawdown'])
        self.assertEqual(report['provenance']['test_status'], 'unknown; tests not executed by report generation')
        self.assertTrue(any(f['subject']=='Coverage' and f['severity']=='critical' for f in report['findings']))

    def test_quote_coinglass_and_missing_market_coverage(self):
        self.doc['observations'] = [self.observation(START+300), self.observation(START+600, False)]
        self.doc['observations'][1]['scan']['top5'].append('ABSENTUSDTM')
        self.doc['observations'][1]['market']['TESTUSDTM']['quote_time'] = START+100
        self.doc['observations'].append(dict(time=START+900, skipped='cycle_error', market={}))
        report = self.report(self.generate()[0])
        data = report['data']
        self.assertEqual(data['cycle_coverage']['observed'], 2)
        self.assertEqual(data['skipped_records'], 1)
        self.assertEqual(data['coins']['TESTUSDTM']['coinglass_missing_rate'], .5)
        self.assertEqual(data['coins']['TESTUSDTM']['stale_or_invalid_quote'], 1)
        self.assertEqual(data['coins']['ABSENTUSDTM']['coinglass_missing_rate'], 1)
        self.assertEqual(data['coins']['ABSENTUSDTM']['stale_or_invalid_quote'], 1)
        self.assertEqual(data['additional_filter_rejections']['coinglass_missing_or_stale'], 2)

    def test_risk_halts_and_deferred_exits_do_not_count_as_trades(self):
        self.doc['events'] = [dict(time=START+1, account='baseline', type='daily_halt'),
                              dict(time=START+2, account='baseline', type='deferred_exit')]
        result = self.report(self.generate()[0])['accounts']['baseline']
        self.assertEqual(result['risk_halts'], 1)
        self.assertEqual(result['deferred_exits'], 1)
        self.assertEqual(result['fills'], 0)
        self.assertEqual(result['closed_trades'], 0)

    def test_pending_ack_and_public_records(self):
        self.generate()
        pending = audits.pending(self.root)
        self.assertEqual(len(pending), 3)
        self.assertTrue(Path(pending[0]['paths']['json']).is_file())
        identifier = pending[0]['id']
        audits.acknowledge(self.root, [identifier])
        self.assertEqual(len(audits.pending(self.root)), 2)
        delivered = next(r['delivered_at'] for r in audits.list_reports(self.root) if r['id']==identifier)
        audits.acknowledge(self.root, [identifier])
        self.assertEqual(next(r['delivered_at'] for r in audits.list_reports(self.root) if r['id']==identifier), delivered)
        for row in audits.list_reports(self.root):
            self.assertNotIn('paths', row)
            self.assertTrue(row['html_url'].startswith('/audits/'))
            self.assertNotIn(str(self.root), json.dumps(row))

    def test_ack_rejects_unknown_and_path_traversal_without_partial_ack(self):
        record = self.generate()[0]
        for identifier in ('../../bad', 'daily-20300101T060000Z'):
            with self.assertRaises(ValueError):
                audits.acknowledge(self.root, [record['id'], identifier])
        self.assertEqual(len(audits.pending(self.root)), 3)

    def test_manifest_commit_failure_keeps_window_due_for_retry(self):
        real = cli.atomic_json
        def fail_manifest(path, value):
            if path.name=='manifest.json':
                raise OSError('fixture')
            return real(path, value)
        with patch.object(audits.cli, 'atomic_json', side_effect=fail_manifest):
            with self.assertRaises(OSError):
                audits.generate_due(self.root, START+48*3600)
        self.assertEqual(audits.list_reports(self.root), [])
        self.assertEqual(len(audits.generate_due(self.root, START+48*3600)), 3)
        self.assertEqual(len(list((self.root/'audits').glob('*.html'))), 3)

    def test_partial_artifact_failure_keeps_manifest_uncommitted(self):
        with patch.object(audits, '_atomic_text', side_effect=OSError('fixture')):
            with self.assertRaises(OSError):
                audits.generate_due(self.root, START+48*3600)
        self.assertEqual(audits.list_reports(self.root), [])
        self.assertEqual(len(audits.generate_due(self.root, START+48*3600)), 3)

    def test_html_escapes_saved_untrusted_strings(self):
        payload = '</pre><script>alert(1)</script><pre>'
        self.doc['errors'] = [dict(time=START+300, type=payload)]
        record = self.generate()[0]
        page = (self.root/'audits'/(record['id']+'.html')).read_text()
        self.assertNotIn('<script>', page)
        self.assertIn('&lt;script&gt;', page)
        self.assertIn('Data coverage and rejected signals', page)
        self.assertIn('Account results', page)

    def test_reads_retained_records_and_deduplicates_hot_replay(self):
        event = dict(time=START+300, account='baseline', type='close', net_pnl=2, exit_fee=.05)
        obs = self.observation(START+300)
        self.doc['events'] = [event]
        self.doc['observations'] = [obs]
        archive_dir = self.root/'experiment-archive'
        archive_dir.mkdir()
        day = datetime.fromtimestamp(START+300, timezone.utc).strftime('%Y-%m-%d')
        cli.atomic_json(archive_dir/(day+'.json'), dict(schema=1, day=day, events=[event], observations=[obs], errors=[]))
        audit = self.report(next(r for r in self.generate() if r['kind']=='audit48h'))
        self.assertEqual(audit['accounts']['baseline']['closed_trades'], 1)
        self.assertEqual(audit['data']['cycle_coverage']['observed'], 1)
        self.doc['events'] = []
        self.doc['observations'] = []
        self.save()
        generated = audits.generate_due(self.root, START+7*86400)
        weekly = next(r for r in generated if r['kind']=='weekly')
        self.assertEqual(self.report(weekly)['accounts']['baseline']['closed_trade_net_pnl'], 2)

    def test_missing_and_corrupt_start_do_not_create_reports(self):
        for value in (None, True, float('inf')):
            self.doc['start_at'] = value
            (self.root/'experiment.json').write_text(json.dumps(self.doc))
            with self.assertRaises(ValueError):
                audits.generate_due(self.root, START+48*3600)
            self.assertEqual(audits.list_reports(self.root), [])

    def test_archive_bound_to_original_start(self):
        self.generate()
        self.doc['start_at'] += 1
        self.save()
        with self.assertRaises(ValueError):
            audits.generate_due(self.root, START+48*3600)

    def test_idle_checks_do_not_read_archive_history(self):
        self.generate()
        with patch.object(audits.metric_evidence, 'load', side_effect=AssertionError('no due report')):
            self.assertEqual(audits.generate_due(self.root, START+48*3600), [])

    def test_next_window_loads_bounded_lifetime_history_and_chains_totals(self):
        self.doc['events'] = [dict(time=START+300, account='baseline', type='close', net_pnl=2)]
        self.generate(START+7*86400)
        while audits.generate_due(self.root, START+7*86400):
            pass
        self.doc['events'] = [dict(time=START+7*86400+300, account='baseline', type='close', net_pnl=-1)]
        self.save()
        real = audits.metric_evidence.load
        with patch.object(audits.metric_evidence, 'load', wraps=real) as read:
            records = audits.generate_due(self.root, START+9*86400)
        self.assertEqual(read.call_args.args[1]['start_at'], START)
        audit = self.report(next(r for r in records if r['kind']=='audit48h'))
        metrics = audit['accounts']['baseline']
        self.assertEqual(metrics['closed_trade_net_pnl'], -1)
        self.assertEqual(metrics['cumulative']['closed_trade_net_since_start'], 1)
        self.assertEqual(metrics['cumulative']['closed_trades_since_start'], 2)

    def test_missing_declared_archive_prevents_misleading_report(self):
        day = datetime.fromtimestamp(START+300, timezone.utc).strftime('%Y-%m-%d')
        self.doc['archive_files'] = [day+'.json']
        self.save()
        with self.assertRaisesRegex(ValueError, 'required observation archive'):
            audits.generate_due(self.root, START+48*3600)
        self.assertEqual(audits.list_reports(self.root), [])

    def test_conflicting_hot_and_archived_observations_fail_visible(self):
        obs = self.observation(START+300)
        self.doc['observations'] = [deepcopy(obs)]
        self.doc['observations'][0]['skipped'] = 'different'
        self.save()
        archive_dir = self.root/'experiment-archive'
        archive_dir.mkdir()
        day = datetime.fromtimestamp(START+300, timezone.utc).strftime('%Y-%m-%d')
        cli.atomic_json(archive_dir/(day+'.json'), dict(schema=1, day=day, events=[], observations=[obs], errors=[]))
        with self.assertRaisesRegex(ValueError, 'conflicting observations'):
            audits.generate_due(self.root, START+48*3600)
        self.assertEqual(audits.list_reports(self.root), [])

    def test_feature_cache_ttl_boundary_is_missing_not_eligible(self):
        obs = self.observation(START+1800)
        obs['coinglass']['fetched_at'] = START
        self.doc['observations'] = [obs]
        report = self.report(self.generate()[0])
        coin = report['data']['coins']['TESTUSDTM']
        self.assertEqual(coin['coinglass_missing_rate'], 1)
        self.assertEqual(coin['filtered_signal_eligible'], 0)

    def test_cli_writes_require_explicit_paper_and_reads_do_not(self):
        with patch('sys.stderr'), self.assertRaises(SystemExit):
            audits.main(['generate', '--runtime', str(self.root)])
        with patch('builtins.print'):
            self.assertEqual(audits.main(['pending', '--runtime', str(self.root)]), 0)
        self.assertFalse((self.root/'audits').exists())


if __name__ == '__main__':
    unittest.main()
