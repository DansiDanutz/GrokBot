"""Publication boundary tests: never copy private runtime documents verbatim."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from paper_grid import public_snapshot as public

AT = 1789080000
SECRET = 'private-api-token-0123456789'


def fixture():
    account = dict(mode='paper', cash=1000, equity=1000, initial_equity=1000,
                   realized_pnl=0, unrealized_net_pnl=0, trade_count=0, positions={},
                   unknown_secret=SECRET, scan={'private_path': '/Users/private/.env'})
    return dict(experiment=dict(mode='paper', status='running', start_at=AT-100,
                    report_at=AT, last_tick_at=AT, continuous=True, end_at=None),
                accounts={a: deepcopy(account) for a in public.experiment.ARMS},
                history=[dict(time=AT, baseline_equity=1000, liquidation_filter_equity=1000, token=SECRET)],
                candidates=[dict(symbol='BTCUSDTM', score=60, baseline_eligible=True,
                    reasons=['wide_spread', SECRET, '<script>alert(1)</script>'], bid_size=SECRET)],
                coinglass=dict(fetched_at=AT, symbols={'BTCUSDTM': dict(eligible=True,
                    reason='liquidation_filter_pass', total_usd=123456789, long_share=0.99,
                    burst_ratio=42, series=[SECRET], token=SECRET)}, api_key=SECRET),
                events=[dict(type='open', account='baseline', symbol='BTCUSDTM', time=AT,
                    fee=0.06, raw={'secret': SECRET})],
                config=dict(public.engine.default_config(), api_key=SECRET),
                errors=[dict(type='coinglass_data', details=SECRET), dict(type=SECRET, body=SECRET)],
                health=dict(last_tick_at=AT, errors=[dict(type='cycle_error', body=SECRET)]),
                limitations=[SECRET], unknown={'secret': SECRET})


def audit_fixture():
    identifier = public.audits._identifier('daily', AT)
    return dict(id=identifier, kind='daily', mode='paper', generated_at=AT,
                window=dict(start_at=AT-86400, end_at=AT, start_local=SECRET),
                accounts={a: dict(equity_change=1.5, closed_trades=2,
                    equity_sample_coverage=dict(observed=48), private=SECRET) for a in public.experiment.ARMS},
                data=dict(cycle_coverage=dict(observed=288), errors=[dict(type=SECRET, detail=SECRET)],
                          raw_coinglass={'total_usd': 123456789}),
                severity='info', summary=SECRET, findings=[dict(subject='Coverage', severity='info', detail=SECRET),
                    dict(subject=SECRET, severity='warning', detail='/Users/private/.env')],
                comparison=dict(filtered_minus_baseline_equity_change=0, interpretation=SECRET), provenance=SECRET)


class PublicSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.runtime = self.root / 'runtime'
        self.runtime.mkdir()
        (self.runtime / 'experiment.json').write_text('{"mode":"paper","secret":"' + SECRET + '"}')
        self.index = self.root / 'index.html'
        self.index.write_text('<!doctype html><title>Paper test</title>')
        self.source = fixture()
        self.rows = []
        self.patches = [patch.object(public, 'PUBLIC_INDEX', self.index),
                        patch.object(public.experiment, 'report', side_effect=lambda *a, **k: deepcopy(self.source)),
                        patch.object(public.audits, 'list_reports', side_effect=lambda *a: deepcopy(self.rows))]
        for item in self.patches:
            item.start()
        self.addCleanup(self.temp.cleanup)
        for item in self.patches:
            self.addCleanup(item.stop)

    def export(self, **kwargs):
        return public.export_snapshot(self.runtime, self.root / 'site', now=AT, **kwargs)

    def contents(self):
        return '\n'.join(p.read_text() for p in (self.root / 'site').rglob('*') if p.is_file())

    def add_audit(self):
        audit = audit_fixture()
        directory = self.runtime / 'audits'
        directory.mkdir(exist_ok=True)
        (directory / (audit['id'] + '.json')).write_text(json.dumps(audit))
        self.rows.append(dict(id=audit['id'], summary=SECRET, html_url='/Users/private/.env'))
        return audit

    def test_paper_landing_and_control_are_distinct_with_missing_snapshot(self):
        self.export()
        stage = self.root / 'site'
        self.assertEqual((stage / 'index.html').read_bytes(),
                         (stage / 'paper/index.html').read_bytes())
        self.assertEqual((stage / 'control/index.html').read_bytes(), self.index.read_bytes())
        self.assertTrue((stage / 'radar/index.html').is_file())
        snapshot = json.loads((stage / 'data/autopilot.json').read_text())
        self.assertEqual(snapshot['status'], 'unavailable')
        self.assertEqual(snapshot['published_at_ms'], AT * 1000)

    def test_autopilot_is_sanitized_and_publication_time_is_not_input_controlled(self):
        from trader.autopilot import policy
        value = policy.snapshot(policy.new_state(1000), 2000,
            dict(heartbeat_ms=2000, tick_age_s=10, kucoin_ok=True, radar_age_min=1))
        value.update(private=SECRET, published_at_ms=7)
        source = self.root / 'autopilot.json'
        source.write_text(json.dumps(value))
        before = source.read_bytes()
        self.export(autopilot_path=source)
        published = json.loads((self.root / 'site/data/autopilot.json').read_text())
        self.assertEqual(published['equity'], 10000)
        self.assertEqual(published['published_at_ms'], AT * 1000)
        self.assertEqual(published['generated_at_ms'], 2000)
        self.assertNotIn(SECRET, json.dumps(published))
        self.assertEqual(source.read_bytes(), before)

    def test_recent_paper_events_are_published_without_private_fields(self):
        from trader.autopilot import policy
        from trader.autopilot.storage import EventLog
        source = self.root / 'autopilot.json'
        source.write_text(json.dumps(policy.snapshot(policy.new_state(1000), 2000, {})))
        EventLog(self.root).append([dict(ts_ms=int(AT*1000), event_id=i, bot_id=1,
            symbol='RAYUSDTM', type='GRID', profit=1.25, diagnostic=123) for i in range(1,61)])
        self.export(autopilot_path=source)
        published = json.loads((self.root / 'site/data/autopilot.json').read_text())
        self.assertTrue(published['recent_events_available'])
        self.assertEqual(len(published['completed_grid_events']), 60)
        self.assertEqual([e['event_id'] for e in published['recent_events']], list(range(11,61)))
        self.assertNotIn('diagnostic', json.dumps(published['recent_events']))
        self.assertEqual(published['account']['starting_equity'], 10000)

    def test_decisions_24h_are_allowlisted_and_capped(self):
        from trader.autopilot import policy
        from trader.autopilot.storage import EventLog
        source = self.root / 'autopilot.json'
        source.write_text(json.dumps(policy.snapshot(policy.new_state(1000), 2000, {})))
        decisions = [dict(ts_ms=int(AT * 1000) - i, event_id=i + 1, bot_id=i + 1,
            symbol='RAYUSDTM', type='DECISION', action='open', direction='LONG',
            radar_direction='LONG', radar_score=50 + i / 100,
            expected_grids_per_hour=10, range_width_pct=8, funding_rate=0,
            kucoin_ok=1, rule_blocks=[]) for i in range(60)]
        EventLog(self.root).append(decisions)
        self.export(autopilot_path=source)
        published = json.loads((self.root / 'site/data/autopilot.json').read_text())
        self.assertEqual(len(published['decisions_24h']), 50)
        self.assertTrue(all(event['type'] == 'DECISION'
                            for event in published['decisions_24h']))
        self.assertIn('radar_score', published['decisions_24h'][0])

    def test_explicit_snapshot_missing_oversized_or_symlink_fails_closed(self):
        target = self.root / 'missing.json'
        with self.assertRaises(ValueError):
            self.export(autopilot_path=target)
        target.write_text(' ' * (2 * 1024 * 1024 + 1))
        with self.assertRaises(ValueError):
            self.export(autopilot_path=target)
        target.unlink()
        target.symlink_to(self.runtime / 'experiment.json')
        with self.assertRaises(ValueError):
            self.export(autopilot_path=target)
        self.assertFalse((self.root / 'site').exists())

    def test_audit_discloses_only_allowlisted_boundary_conventions(self):
        for boundary in ('[start, end)', '(start, end]', None, SECRET, ['invalid']):
            with self.subTest(boundary=boundary):
                source = audit_fixture()
                if boundary is not None:
                    source['window']['boundary_convention'] = boundary
                value = public._audit(source, source['id'], AT)
                expected = boundary if boundary in ('[start, end)', '(start, end]') else 'unknown'
                self.assertEqual(value['window']['boundary_convention'], expected)
                self.assertNotIn(SECRET, json.dumps(value))

    def test_daily_chart_labels_match_bucharest_buckets(self):
        for path in ('dashboard.html', 'public/index.html'):
            source = (Path(__file__).parent / path).read_text()
            self.assertNotIn('UTC day', source)
            self.assertIn('Bucharest day', source)

    def test_explicit_dto_excludes_private_and_provider_data(self):
        metadata = self.export()
        text = self.contents()
        for banned in (SECRET, '/Users/', '123456789', 'burst_ratio', 'long_share', 'api_key', '<script>alert(1)</script>'):
            self.assertNotIn(banned, text)
        report = json.loads((self.root / 'site/data/report.json').read_text())
        self.assertEqual(report['mode'], 'paper')
        self.assertEqual(report['published_at'], AT)
        self.assertEqual(report['accounts']['baseline']['equity'], 1000)
        self.assertEqual(report['coinglass']['symbols']['BTCUSDTM'],
                         dict(eligible=True, reason='liquidation_filter_pass'))
        self.assertEqual(metadata['file_count'], 12)
        self.assertIsNone(report['health']['worker_alive'])

    def test_health_is_allowlisted_not_exception_text(self):
        self.export(health=dict(worker_alive=True, last_error=SECRET, last_audit_error='/Users/private/key',
                               server_time=AT, last_poll_at=AT, errors=[dict(type='cycle_error', details=SECRET)]))
        value = json.loads((self.root / 'site/data/health.json').read_text())
        self.assertTrue(value['worker_alive'])
        self.assertTrue(value['collection_error'])
        self.assertTrue(value['audit_error'])
        self.assertEqual(value['errors'], [dict(category='cycle_error', count=1)])

    def test_analytics_failure_keeps_portfolio_and_hides_exception(self):
        with patch.object(public.analytics, 'build', side_effect=ValueError(SECRET)):
            self.export()
        value = json.loads((self.root / 'site/data/analytics.json').read_text())
        self.assertEqual(value['status'], 'unavailable')
        self.assertNotIn(SECRET, json.dumps(value))
        self.assertTrue((self.root / 'site/data/report.json').is_file())

    def test_analytics_dto_is_published_as_separate_file(self):
        value = dict(schema=1, mode='paper', status='ok', generated_at=AT, windows={})
        with patch.object(public.analytics, 'build', return_value=value):
            self.export()
        self.assertEqual(json.loads((self.root / 'site/data/analytics.json').read_text()), value)
        self.assertNotIn(SECRET, self.contents())

    def test_radar_is_allowlisted_and_exported_as_a_page(self):
        radar = self.runtime.parent / 'radar'
        radar.mkdir()
        payload = dict(schema_version=1, generated_at_ms=AT * 1000, asof_ms=AT * 1000,
            constants={}, filters={}, rows=[], sections={'long': [dict(symbol='BTCUSDTM',
            direction='LONG', price=50_000, turnover_24h_usdt=10_000_000,
            spread_pct=.01, snapshot_age_min=4, funding_pct=.01, listing_age_days=30, atr_1h_pct=1,
            atr_4h_pct=2, slope_4h_pct=.3, position_7d=.5, change_24h_pct=2,
            low_7d=40_000, high_7d=55_000, range_low=48_000, range_high=55_000,
            step_pct=.8, grids=17, expected_grids_per_hour=2.4, rank_score=2.4,
            grid_interval=400, profit_pct_min=1.01, profit_pct_max=1.5,
            passes_liquidity=True)]})
        payload['private'] = SECRET
        (radar / 'radar.json').write_text(json.dumps(payload))
        self.export()
        self.assertTrue((self.root / 'site/radar/index.html').is_file())
        exported = json.loads((self.root / 'site/data/radar.json').read_text())
        self.assertEqual(exported['sections']['long'][0]['direction'], 'LONG')
        self.assertEqual(exported['sections']['long'][0]['snapshot_age_min'], 4)
        self.assertEqual(exported['sections']['long'][0]['profit_pct_min'], 1.01)
        self.assertEqual(exported['sections']['long'][0]['grid_interval'], 400)
        self.assertNotIn(SECRET, json.dumps(exported))

    def test_audits_regenerated_from_numeric_dto(self):
        audit = self.add_audit()
        for ext in ('.html', '.md'):
            (self.runtime / 'audits' / (audit['id'] + ext)).write_text(SECRET + '<script>evil</script>')
        self.export()
        self.assertNotIn(SECRET, self.contents())
        for archive in (self.root / 'site/reports').glob('*'):
            self.assertNotIn('<script>', archive.read_text())
        value = json.loads((self.root / 'site/reports' / (audit['id'] + '.json')).read_text())
        self.assertEqual(value['accounts']['baseline']['equity_change'], 1.5)
        self.assertEqual(value['findings'], [dict(subject='Coverage', severity='info')])
        index = json.loads((self.root / 'site/data/audits.json').read_text())
        self.assertEqual(index[0]['html_url'], '/reports/' + audit['id'] + '.html')

    def test_no_runtime_mutation(self):
        self.add_audit()
        before = {str(p.relative_to(self.runtime)): (p.read_bytes(), p.stat().st_mtime_ns)
                  for p in self.runtime.rglob('*') if p.is_file()}
        self.export()
        after = {str(p.relative_to(self.runtime)): (p.read_bytes(), p.stat().st_mtime_ns)
                 for p in self.runtime.rglob('*') if p.is_file()}
        self.assertEqual(before, after)

    def test_nonfinite_public_numbers_rejected_before_writes(self):
        for value in (float('nan'), float('inf'), -float('inf'), 1e30, True, SECRET):
            with self.subTest(value=value):
                self.source['accounts']['baseline']['cash'] = value
                with self.assertRaises(ValueError):
                    self.export()
                self.assertFalse((self.root / 'site').exists())

    def test_unknown_mode_refused(self):
        self.source['experiment']['mode'] = 'live'
        with self.assertRaises(ValueError):
            self.export()

    def test_nonpaper_account_refused(self):
        self.source['accounts']['baseline']['mode'] = 'live'
        with self.assertRaises(ValueError):
            self.export()

    def test_timestamps_and_unsafe_ids_refused(self):
        for value in ('2026-09-11', -1, 1e15, float('nan')):
            with self.subTest(timestamp=value):
                self.source['experiment']['start_at'] = value
                with self.assertRaises(ValueError):
                    self.export()
        self.source = fixture()
        for value in ('../experiment', 'daily-20269999T000000Z', 'daily-20260911T000000Z/../../x'):
            with self.subTest(identifier=value):
                self.rows = [dict(id=value)]
                with self.assertRaises(ValueError):
                    self.export()

    def test_audit_end_and_identifier_must_match(self):
        audit = self.add_audit()
        audit['window']['end_at'] += 3600
        (self.runtime / 'audits' / (audit['id'] + '.json')).write_text(json.dumps(audit))
        with self.assertRaises(ValueError):
            self.export()

    def test_symlink_report_refused(self):
        audit = self.add_audit()
        path = self.runtime / 'audits' / (audit['id'] + '.json')
        path.unlink()
        path.symlink_to(self.runtime / 'experiment.json')
        with self.assertRaises(ValueError):
            self.export()

    def test_symlink_runtime_refused(self):
        path = self.root / 'alias'
        path.symlink_to(self.runtime)
        with self.assertRaises(ValueError):
            public.export_snapshot(path, self.root / 'site', now=AT)

    def test_symlink_output_refused(self):
        (self.root / 'site').symlink_to(self.runtime)
        with self.assertRaises(ValueError):
            self.export()

    def test_runtime_output_overlap_refused(self):
        for target in (self.runtime, self.runtime / 'site', self.root):
            with self.subTest(target=target):
                with self.assertRaises(ValueError):
                    public.export_snapshot(self.runtime, target, now=AT)

    def test_existing_output_cannot_publish_unrelated_assets(self):
        site = self.root / 'site'
        site.mkdir()
        (site / '.env').write_text(SECRET)
        with self.assertRaises(ValueError):
            self.export()
        self.assertEqual((site / '.env').read_text(), SECRET)

    def test_history_and_events_bounded(self):
        self.source['history'] *= 1000
        self.source['events'] *= 1000
        self.export()
        value = json.loads((self.root / 'site/data/report.json').read_text())
        self.assertEqual(len(value['history']), 336)
        self.assertEqual(len(value['events']), 40)

    def test_size_limit_before_writes(self):
        with patch.object(public, 'MAX_BYTES', 10):
            with self.assertRaises(ValueError):
                self.export()
        self.assertFalse((self.root / 'site').exists())

    def test_unsafe_symbol_refused(self):
        self.source['candidates'][0]['symbol'] = '../../.env'
        with self.assertRaises(ValueError):
            self.export()


if __name__ == '__main__':
    unittest.main()
