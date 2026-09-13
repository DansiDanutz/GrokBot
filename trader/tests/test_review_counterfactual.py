"""Offline tests for trader.review.counterfactual — synthetic candles only.

Builds the candidate records with the real recorder, replays them on hand-made
1m paths, and checks the daily-review wiring. Never reads live runtime files.
"""
from argparse import Namespace
from contextlib import closing, redirect_stdout
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from trader.autopilot import counterfactual as recorder
from trader.autopilot import policy
from trader.data.store import Store
from trader.review import counterfactual as replay_module
from trader.review import daily, rules as learned_rules
from trader.tests.test_autopilot_policy import row
from trader.tests.test_counterfactual import skip_event

MINUTE = 60_000
HOUR = 3_600_000
NOW = 1_789_257_600_000  # 2026-09-13T00:00:00Z
DATE = '2026-09-13'


def candidate(symbol='A', direction='LONG', block='bot_capacity', ts_ms=NOW, **extra):
    built = recorder.candidates([row(symbol, direction, **extra)],
                                [skip_event(symbol, direction,
                                            [policy.DECISION_RULES[block]], ts_ms)],
                                'scan-1', ts_ms)
    assert built, 'fixture must produce a replayable candidate'
    return built[0]


def candles(start_ms, prices):
    """1m candles (ts_ms are close times) whose closes follow `prices`."""
    rows, previous = [], prices[0]
    for index, price in enumerate(prices):
        rows.append(dict(ts_ms=start_ms + (index + 1) * MINUTE, open=previous,
                         high=max(previous, price), low=min(previous, price), close=price))
        previous = price
    return rows


def oscillating(count):
    return [99.0 if index % 2 else 101.0 for index in range(count)]


def trending(count):
    return [min(100.0 + index, 140.0) for index in range(count)]


class ReplayTests(unittest.TestCase):
    def test_oscillating_path_completes_grids_and_exits_at_the_horizon(self):
        outcome = replay_module.replay(candidate(), candles(NOW, oscillating(120)), horizon_h=2)

        self.assertEqual(outcome['exit_reason'], 'HORIZON')
        self.assertGreater(outcome['grids'], 0)
        self.assertGreater(outcome['grid_profit'], 0)
        self.assertGreater(outcome['grids_per_hour'], 0)
        self.assertAlmostEqual(outcome['hold_h'], 2.0, places=4)
        self.assertFalse(outcome['partial'])
        self.assertEqual((outcome['symbol'], outcome['direction']), ('A', 'LONG'))
        self.assertEqual(outcome['rule_blocks'], [policy.DECISION_RULES['bot_capacity']])
        self.assertEqual(set(outcome), {'symbol', 'direction', 'rule_blocks', 'hold_h', 'grids',
                                        'grid_profit', 'fees', 'funding', 'net',
                                        'grids_per_hour', 'exit_reason', 'partial', 'ts_ms',
                                        'price', 'exit_price'})

    def test_trending_path_exits_on_a_range_break_before_the_horizon(self):
        outcome = replay_module.replay(candidate(), candles(NOW, trending(120)), horizon_h=2)

        self.assertEqual(outcome['exit_reason'], 'RANGE_BREAK')
        self.assertLess(outcome['hold_h'], 2.0)
        self.assertGreater(outcome['hold_h'], 0.0)
        self.assertFalse(outcome['partial'])

    def test_a_collapse_inside_the_range_rides_on_like_the_live_desk(self):
        """Corrected 2026-09-14: this used to assert a STOP_LOSS exit.

        The live desk discards every STOP_LOSS the engine emits and exits on the
        structural boundary instead. A replay that cut the loser here would cap
        its loss near -12% while the real bot kept going, flattering every
        replayed loss and understating what refusing the entry cost.
        """
        outcome = replay_module.replay(candidate(), candles(NOW, [95.0, 85.0, 79.6, 79.6]),
                                       horizon_h=2)

        self.assertEqual(outcome['exit_reason'], 'HORIZON')
        self.assertLess(outcome['net'], 0.0)

    def test_short_candle_coverage_is_replayed_and_flagged_partial(self):
        outcome = replay_module.replay(candidate(), candles(NOW, oscillating(30)), horizon_h=2)

        self.assertEqual(outcome['exit_reason'], 'HORIZON')
        self.assertTrue(outcome['partial'])

    def test_candles_outside_the_horizon_are_ignored(self):
        inside = candles(NOW, oscillating(120))
        outside = candles(NOW + 2 * HOUR, trending(120))

        outcome = replay_module.replay(candidate(), inside + outside, horizon_h=2)

        self.assertEqual(outcome['exit_reason'], 'HORIZON')

    def test_replay_never_mutates_the_candidate(self):
        record = candidate()
        before = json.dumps(record, sort_keys=True)

        replay_module.replay(record, candles(NOW, oscillating(30)), horizon_h=2)

        self.assertEqual(json.dumps(record, sort_keys=True), before)


class SummarizeTests(unittest.TestCase):
    def outcome(self, net, direction='LONG', blocks=(3,), gph=1.0):
        return dict(symbol='A', direction=direction, rule_blocks=list(blocks), hold_h=1.0,
                    grids=1, grid_profit=0.0, fees=0.0, funding=0.0, net=net,
                    grids_per_hour=gph, exit_reason='HORIZON', partial=False)

    def test_empty_input_yields_a_zeroed_headline(self):
        summary = replay_module.summarize([])

        self.assertEqual(summary['total'],
                         {'count': 0, 'sum_net': 0.0, 'median_grids_per_hour': 0.0,
                          'share_with_positive_net': 0.0})
        self.assertEqual(summary['by_rule_block'], {})

    def test_aggregates_per_rule_block_and_direction(self):
        outcomes = [self.outcome(10.0, 'LONG', [3], 2.0),
                    self.outcome(-4.0, 'SHORT', [3, 7], 4.0),
                    self.outcome(6.0, 'SHORT', [7], 6.0)]

        summary = replay_module.summarize(outcomes)

        self.assertEqual(summary['total']['count'], 3)
        self.assertEqual(summary['total']['sum_net'], 12.0)
        self.assertEqual(summary['total']['median_grids_per_hour'], 4.0)
        self.assertAlmostEqual(summary['total']['share_with_positive_net'], 2 / 3, places=4)
        self.assertEqual(summary['by_rule_block']['bot_capacity'],
                         {'count': 2, 'sum_net': 6.0, 'median_grids_per_hour': 3.0,
                          'share_with_positive_net': 0.5})
        self.assertEqual(summary['by_rule_block']['direction_cap']['count'], 2)
        self.assertEqual(summary['by_direction']['SHORT']['sum_net'], 2.0)
        self.assertEqual(summary['by_direction']['LONG']['count'], 1)


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.database = self.root / 'market.sqlite3'
        with Store(self.database) as store:
            store.upsert('klines', [
                dict(symbol='A', interval='1m', time_ms=NOW + index * MINUTE,
                     open=price, high=price, low=price, close=price, volume=1.0, turnover=1.0)
                for index, price in enumerate(oscillating(120))])
            store.upsert('klines', [dict(symbol='A', interval='1h', time_ms=NOW, open=1.0,
                                         high=1.0, low=1.0, close=1.0, volume=1.0, turnover=1.0)])

    def test_load_candles_reads_only_completed_1m_bars_in_range(self):
        rows = replay_module.load_candles(self.database, 'A', NOW, NOW + 2 * HOUR)

        self.assertEqual(len(rows), 120)
        self.assertEqual(rows[0]['ts_ms'], NOW + MINUTE)
        self.assertEqual(rows[-1]['ts_ms'], NOW + 120 * MINUTE)
        self.assertEqual(set(rows[0]), {'ts_ms', 'open', 'high', 'low', 'close'})

    def test_load_candles_parameterizes_the_symbol_and_cannot_write(self):
        self.assertEqual(replay_module.load_candles(
            self.database, "A'); DELETE FROM klines;--", NOW, NOW + HOUR), [])
        self.assertEqual(len(replay_module.load_candles(self.database, 'A', NOW, NOW + 2 * HOUR)),
                         120)
        with closing(sqlite3.connect(self.database.as_uri() + '?mode=ro', uri=True)) as ro:
            with self.assertRaises(sqlite3.OperationalError):
                ro.execute('DELETE FROM klines')

    def test_cli_writes_the_report_atomically(self):
        directory = self.root / 'counterfactual'
        recorder.append(directory, [candidate(), candidate(direction='NEUTRAL')], NOW)
        out = self.root / 'reports' / f'counterfactual-{DATE}.json'

        with redirect_stdout(io.StringIO()):
            code = replay_module.main(['--database', str(self.database),
                                       '--candidates-dir', str(directory), '--date', DATE,
                                       '--out', str(out), '--horizon-hours', '2'])

        self.assertEqual(code, 0)
        report = json.loads(out.read_text())
        self.assertEqual(report['schema_version'], 1)
        self.assertEqual((report['date'], report['candidates'], report['replayed']),
                         (DATE, 2, 2))
        self.assertEqual(len(report['outcomes']), 2)
        self.assertEqual(report['summary']['total']['count'], 2)
        self.assertTrue(report['generated_at_ms'] > 0)

    def test_cli_on_a_missing_day_reports_nothing_replayed(self):
        out = self.root / 'reports' / 'counterfactual-2026-01-01.json'

        with redirect_stdout(io.StringIO()):
            code = replay_module.main(['--database', str(self.database),
                                       '--candidates-dir', str(self.root / 'counterfactual'),
                                       '--date', '2026-01-01', '--out', str(out)])

        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.read_text())['replayed'], 0)

    def test_cli_rejects_a_malformed_date(self):
        with self.assertRaises(SystemExit):
            replay_module.main(['--database', str(self.database), '--candidates-dir',
                                str(self.root), '--date', '13-09-2026', '--out',
                                str(self.root / 'x.json')])


def report(net_by_block):
    outcomes = [dict(symbol='A', direction='LONG', rule_blocks=[policy.DECISION_RULES[name]],
                     hold_h=1.0, grids=2, grid_profit=net, fees=0.0, funding=0.0, net=net,
                     grids_per_hour=2.0, exit_reason='HORIZON', partial=False)
                for name, net in net_by_block.items()]
    return {'schema_version': 1, 'date': DATE, 'generated_at_ms': NOW,
            'candidates': len(outcomes), 'replayed': len(outcomes), 'horizon_hours': 24,
            'outcomes': outcomes, 'summary': replay_module.summarize(outcomes)}


class AdvisoryTests(unittest.TestCase):
    def test_capacity_cost_fires_only_above_the_threshold(self):
        self.assertEqual(learned_rules.counterfactual_proposals(
            report({'bot_capacity': 24.0})), [])

        notes = learned_rules.counterfactual_proposals(
            report({'bot_capacity': 20.0, 'direction_cap': 10.0}))

        self.assertEqual([note['rule'] for note in notes], ['capacity_cost'])
        self.assertEqual(notes[0]['tier'], 2)
        self.assertIn('30.00', notes[0]['evidence'])

    def test_learned_rule_cost_fires_only_above_the_threshold(self):
        self.assertEqual(learned_rules.counterfactual_proposals(
            report({'learned_min_hold': 25.0})), [])

        notes = learned_rules.counterfactual_proposals(
            report({'learned_min_hold': 30.0, 'learned_trend_alignment': 5.0}))

        self.assertEqual([note['rule'] for note in notes], ['learned_rule_cost'])
        self.assertIn('learned', notes[0]['evidence'])

    def test_losing_replays_and_missing_input_raise_nothing(self):
        self.assertEqual(learned_rules.counterfactual_proposals(None), [])
        self.assertEqual(learned_rules.counterfactual_proposals({}), [])
        self.assertEqual(learned_rules.counterfactual_proposals(
            report({'bot_capacity': -100.0})), [])


class ReviewSectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        database = Path(self.temp.name).resolve() / 'market.sqlite3'
        with Store(database):
            pass
        self.conn = daily.open_market_db(database)
        self.addCleanup(self.conn.close)
        self.state = {'open_bots': [], 'closed_bots': []}

    def build(self, counterfactual=None):
        return daily.build_report(DATE, self.state, self.conn, [],
                                  counterfactual=counterfactual)

    def test_absent_counterfactual_leaves_the_report_unchanged(self):
        data = self.build()

        self.assertIsNone(data['counterfactual'])
        self.assertNotIn('Skipped decisions, replayed', daily.render_markdown(data))

    def test_section_renders_counts_net_and_grids_per_hour(self):
        data = self.build(report({'bot_capacity': 40.0, 'direction_cap': -5.0}))

        markdown = daily.render_markdown(data)

        self.assertIn('Skipped decisions, replayed', markdown)
        self.assertIn('bot_capacity', markdown)
        self.assertIn('direction_cap', markdown)
        self.assertIn('35.00', markdown)

    def test_advisory_note_reaches_the_proposals(self):
        data = self.build(report({'bot_capacity': 40.0}))

        rules = [proposal['rule'] for proposal in data['proposals']]

        self.assertIn('capacity_cost', rules)
        self.assertTrue(all(proposal['tier'] == 2 for proposal in data['proposals']
                            if proposal['rule'] == 'capacity_cost'))
        self.assertIn('the slot caps turned away profitable entries',
                      daily.render_markdown(data))


class LoadCounterfactualTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.reports = Path(self.temp.name).resolve()
        self.args = Namespace(out=str(self.reports / f'review-{DATE}.md'), date=DATE)

    def write(self, name, payload):
        path = self.reports / name
        path.write_text(payload if isinstance(payload, str) else json.dumps(payload))
        return path

    def test_default_path_beside_the_markdown_report_is_read(self):
        payload = report({'bot_capacity': 10.0})
        self.write(f'counterfactual-{DATE}.json', payload)

        self.assertEqual(daily.load_counterfactual(None, self.args), payload)

    def test_missing_unreadable_and_wrong_day_files_are_ignored(self):
        self.assertIsNone(daily.load_counterfactual(None, self.args))
        self.write(f'counterfactual-{DATE}.json', 'not json')
        self.assertIsNone(daily.load_counterfactual(None, self.args))
        other = self.write('other.json', report({'bot_capacity': 10.0}) | {'date': '2020-01-01'})
        self.assertIsNone(daily.load_counterfactual(str(other), self.args))


if __name__ == '__main__':
    unittest.main()


class SoleBlockAttributionTests(unittest.TestCase):
    """An advisory may only claim money a single rule change could collect.

    The first real record on 2026-09-13 was a second MYXUSDTM short on a coin
    the desk already held, refused by the slot cap AND the duplicate rule.
    Counted under the slot cap it proposes raising a cap that would still not
    have opened it.
    """

    def outcome(self, blocks, net):
        return dict(symbol='MYXUSDTM', direction='SHORT', rule_blocks=blocks,
                    net=net, grids=10, grids_per_hour=1.0, hold_h=24.0,
                    grid_profit=net, fees=0.0, funding=0.0, exit_reason='HORIZON')

    def test_a_single_reason_is_attributed_to_that_rule(self):
        summary = replay_module.summarize([self.outcome([3], 40.0)])
        self.assertIn('bot_capacity', summary['by_sole_block'])
        self.assertEqual(summary['by_sole_block']['bot_capacity']['sum_net'], 40.0)

    def test_two_reasons_are_attributed_to_neither(self):
        summary = replay_module.summarize([self.outcome([3, 5], 40.0)])
        self.assertEqual(summary['by_sole_block'], {})
        self.assertIn('bot_capacity', summary['by_rule_block'])
        self.assertIn('duplicate_symbol', summary['by_rule_block'])

    def test_the_headline_total_still_counts_every_entry_once(self):
        summary = replay_module.summarize(
            [self.outcome([3], 10.0), self.outcome([3, 5], 40.0)])
        self.assertEqual(summary['total']['count'], 2)
        self.assertEqual(summary['total']['sum_net'], 50.0)

    def test_a_repeated_code_is_still_a_single_reason(self):
        summary = replay_module.summarize([self.outcome([3, 3], 40.0)])
        self.assertIn('bot_capacity', summary['by_sole_block'])


class SoleBlockAdvisoryTests(unittest.TestCase):
    def note_names(self, outcomes):
        summary = replay_module.summarize(outcomes)
        return [note['rule'] for note in
                learned_rules.counterfactual_proposals({'summary': summary})]

    def outcome(self, blocks, net):
        return dict(symbol='X', direction='LONG', rule_blocks=blocks, net=net,
                    grids=10, grids_per_hour=1.0, hold_h=24.0, grid_profit=net,
                    fees=0.0, funding=0.0, exit_reason='HORIZON')

    def test_capacity_cost_fires_on_money_the_cap_alone_refused(self):
        self.assertIn('capacity_cost', self.note_names([self.outcome([3], 90.0)]))

    def test_capacity_cost_stays_quiet_on_money_two_rules_refused(self):
        self.assertNotIn('capacity_cost', self.note_names([self.outcome([3, 5], 90.0)]))


class ReplayMatchesTheLiveExitRules(unittest.TestCase):
    """The replay must not apply a stop the desk deliberately ignores.

    policy.advance drops every STOP_LOSS the engine emits (commit 5693251: the
    boundary stop replaced the cash-loss trigger). A replay that honoured it
    would cut losers near -12% while the real bot rides to the range boundary,
    understating what refusing an entry actually cost.
    """

    def test_stop_loss_is_not_an_exit_signal(self):
        self.assertEqual(replay_module.SIGNAL_EXITS, ('RANGE_BREAK',))

    def test_the_replay_agrees_with_what_policy_acts_on(self):
        from trader.autopilot import policy
        import inspect
        source = inspect.getsource(policy.advance)
        self.assertIn("e['type'] != 'STOP_LOSS'", source,
                      'policy stopped discarding STOP_LOSS; revisit SIGNAL_EXITS')
