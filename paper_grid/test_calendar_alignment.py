"""One Bucharest civil day for halts, daily reports and chart buckets."""
from datetime import datetime
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from paper_grid import analytics, audits, cli, engine, experiment, retention


def timestamp(text):
    return datetime.fromisoformat(text).timestamp()


def document(start, events=(), observations=()):
    return dict(mode='paper', start_at=start, config=engine.default_config(),
                tick_seconds=300, report_seconds=1800, events=list(events), errors=[],
                observations=list(observations), history=[],
                accounts={arm: dict(statistics={'initial_equity': 1000}) for arm in audits.ARMS})


class CalendarAlignmentTests(unittest.TestCase):
    def test_0859_and_0901_share_midnight_daily_boundary(self):
        expected = timestamp('2026-09-12T00:00:00+03:00')
        for text in ('2026-09-11T08:59:00+03:00', '2026-09-11T09:01:00+03:00'):
            at = timestamp(text)
            self.assertEqual(audits._next('daily', at), expected)
            self.assertEqual(engine.initial_state({}, at)['day'], '2026-09-11')

    def test_halt_persists_across_nine_and_resets_only_at_midnight(self):
        at = timestamp('2026-09-11T08:59:00+03:00')
        state = engine.initial_state({}, at)
        state['cash'] = 960
        state, events = engine.step(state, {}, at, {})
        self.assertEqual([event['type'] for event in events], ['daily_halt'])
        for text in ('2026-09-11T09:01:00+03:00', '2026-09-11T23:59:59+03:00'):
            state, events = engine.step(state, {}, timestamp(text), {})
            self.assertEqual(state['halted_day'], '2026-09-11')
            self.assertFalse(any(event['type'] == 'daily_halt' for event in events))
        state, _ = engine.step(state, {}, timestamp('2026-09-11T21:00:00+00:00'), {})
        self.assertEqual(state['day'], '2026-09-12')
        self.assertIsNone(state['halted_day'])

    def test_timezone_override_cannot_split_halt_and_reporting_days(self):
        for zone in ('UTC', 'America/New_York'):
            with self.subTest(zone=zone), self.assertRaisesRegex(ValueError, 'Europe/Bucharest'):
                engine.initial_state({'timezone': zone}, timestamp('2026-09-11T21:00:00+00:00'))

    def test_dst_daily_midnights_are_23_and_25_hours(self):
        for text, hours in (('2026-03-29T00:00:00+02:00', 23),
                            ('2026-10-25T00:00:00+03:00', 25)):
            start = timestamp(text)
            self.assertEqual(audits._next('daily', start)-start, hours*3600)
            self.assertEqual(audits._next('audit48h', start)-start, 48*3600)
        monday = timestamp('2026-09-14T09:00:00+03:00')
        self.assertEqual(audits._next('weekly', monday-1), monday)

    def test_midnight_halt_belongs_only_to_new_daily_report(self):
        start = timestamp('2026-09-11T00:00:00+03:00')
        boundary = timestamp('2026-09-12T00:00:00+03:00')
        end = timestamp('2026-09-13T00:00:00+03:00')
        event = dict(type='daily_halt', account='baseline', time=boundary)
        close = dict(type='close', account='baseline', symbol='TESTUSDTM', time=boundary,
                     net_pnl=1, entry_fees=0, exit_fee=0, funding_model_cost=0)
        observations = [dict(time=at, market={}, telemetry_schema=1) for at in (start, boundary, end)]
        doc = document(start, [event, close], observations)
        first = audits._build(doc, 'daily', start, boundary, end)
        second = audits._build(doc, 'daily', boundary, end, end, first)
        self.assertEqual(first['accounts']['baseline']['risk_halts'], 0)
        self.assertEqual(second['accounts']['baseline']['risk_halts'], 1)
        self.assertEqual(first['accounts']['baseline']['closed_trades'], 0)
        self.assertEqual(second['accounts']['baseline']['closed_trades'], 1)
        self.assertEqual(first['accounts']['baseline']['performance']['closed_trades'], 0)
        self.assertEqual(second['accounts']['baseline']['performance']['closed_trades'], 1)
        self.assertEqual(first['data']['cycle_coverage']['first_at'], start)
        self.assertEqual(second['data']['cycle_coverage']['first_at'], boundary)
        self.assertEqual(first['window']['boundary_convention'], '[start, end)')
        self.assertEqual(second['data']['observation_records'], 1)
        self.assertEqual(first['accounts']['baseline']['buy_rejections']['instrumented_checks'], 1)

    def test_generation_preserves_exact_start_midnight_event(self):
        start = timestamp('2026-09-11T00:00:00+03:00')
        end = timestamp('2026-09-12T00:00:00+03:00')
        event = dict(type='daily_halt', account='baseline', time=start)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            cli.atomic_json(root/'experiment.json', document(start, [event]))
            records = audits.generate_due(root, end)
            row = next(record for record in records if record['kind'] == 'daily')
            result = json.loads((root/'audits'/(row['id']+'.json')).read_text())
            self.assertEqual(result['accounts']['baseline']['risk_halts'], 1)

    def test_chart_day_uses_bucharest_while_archive_day_remains_utc(self):
        at = timestamp('2026-09-11T21:00:00+00:00')
        doc = document(at, observations=[dict(time=at, market={}, scan={})])
        result = analytics._window(doc, at, at, {arm: [] for arm in audits.ARMS})
        self.assertEqual(result['daily'][0]['day'], '2026-09-12')
        self.assertEqual(result['daily'][0]['checks'], 1)
        self.assertEqual(retention._day(at), '2026-09-11')

    def test_empty_chart_days_follow_local_dst_calendar(self):
        start = timestamp('2026-03-28T23:30:00+02:00')
        end = timestamp('2026-03-30T00:30:00+03:00')
        result = analytics._window(document(start), start, end, {arm: [] for arm in audits.ARMS})
        self.assertEqual([day['day'] for day in result['daily']], ['2026-03-28', '2026-03-29', '2026-03-30'])

    def test_shared_calendar_helper_is_part_of_experiment_seal(self):
        sealed = experiment._code_hashes()
        self.assertIn('calendar_day.py', sealed)
        expected = hashlib.sha256((Path(experiment.__file__).parent/'calendar_day.py').read_bytes()).hexdigest()
        self.assertEqual(sealed['calendar_day.py'], expected)


if __name__ == '__main__':
    unittest.main()
