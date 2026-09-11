"""Deterministic replay checks; no provider, account or runtime access."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from paper_grid import engine, replay

T = 1_800_000_000.0


def quote(price=100, now=T, **changes):
    row = dict(symbol='A', bid=price, ask=price * 1.0001, mark=price,
               bid_size=1000000, ask_size=1000000, quote_time=now, lot_size=1,
               multiplier=.001, tick_size=.001, funding_rate=.0001,
               funding_interval_hours=8, score=70, eligible=True, add_eligible=False,
               atr_pct=2., entry_edge_pct=4., turnover24h=10000000.)
    row.update(changes)
    return row


def features(at=T):
    return dict(fetched_at=at, symbols={'A': dict(eligible=True, latest_hour=at - 3600,
                                               burst_ratio=4., long_share=.8, total_usd=10000.)})


def fixture():
    config = engine.default_config()
    return dict(schema=1, mode='paper', start_at=T, config=config,
                initial_state=engine.initial_state(config, T), observations=[
                    dict(time=T, market={'A': quote()}, coinglass=features()),
                    dict(time=T+300, market={'A': quote(103, T+300)}, coinglass=features(T+300))])


class ReplayTests(unittest.TestCase):
    def test_repeatable_pure_and_exact_engine_result_with_costs(self):
        doc = fixture()
        before = deepcopy(doc)
        first = replay.replay_fixture(doc)
        self.assertEqual(first, replay.replay_fixture(doc))
        self.assertEqual(doc, before)
        state = deepcopy(doc['initial_state'])
        events = []
        for row in doc['observations']:
            state, generated = engine.step(state, row['market'], row['time'], doc['config'])
            events.extend(generated)
        baseline = first['accounts']['baseline']
        self.assertEqual(baseline['final_state'], state)
        self.assertEqual(baseline['events'], events)
        self.assertEqual(baseline['trades'], 1)
        self.assertEqual(baseline['wins'], 1)
        self.assertGreater(baseline['execution_fees'], 0)
        self.assertGreater(baseline['funding_model_cost'], 0)
        self.assertEqual(baseline['equity_history'][-1]['equity'], state['cash'])
        self.assertEqual(first['accounts']['baseline'], first['accounts']['liquidation_filter'])

    def test_filter_requires_saved_fresh_features(self):
        doc = fixture()
        for row in doc['observations']:
            row['coinglass'] = {}
        result = replay.replay_fixture(doc)
        self.assertEqual(result['accounts']['baseline']['entries'], 1)
        self.assertEqual(result['accounts']['liquidation_filter']['entries'], 0)
        self.assertEqual(result['accounts']['liquidation_filter']['ending_equity'], 1000)

    def test_sort_dedup_and_conflict(self):
        doc = fixture()
        expected = replay.replay_fixture(doc)['accounts']
        doc['observations'].reverse()
        doc['observations'].append(deepcopy(doc['observations'][0]))
        result = replay.replay_fixture(doc)
        self.assertEqual(result['observations'], 2)
        self.assertEqual(result['accounts'], expected)
        doc['observations'][-1]['market']['A']['bid'] = 99
        with self.assertRaisesRegex(ValueError, 'conflicting'):
            replay.replay_fixture(doc)

    def test_skip_aborted_tick_and_future_quotes_fail_engine_gates(self):
        doc = fixture()
        doc['observations'][0]['skipped'] = 'cycle_error'
        doc['observations'][1]['market']['A']['quote_time'] += 1
        result = replay.replay_fixture(doc)
        arm = result['accounts']['baseline']
        self.assertEqual(result['skipped'], 1)
        self.assertEqual(arm['ticks'], 1)
        self.assertEqual(arm['entries'], 0)
        self.assertEqual(arm['equity_history'][0]['future_quote_symbols'], ['A'])

    def test_missing_held_quote_is_explicit(self):
        doc = fixture()
        doc['observations'][1]['market'] = {}
        arm = replay.replay_fixture(doc)['accounts']['baseline']
        self.assertTrue(arm['coverage_incomplete'])
        self.assertEqual(arm['equity_history'][-1]['unpriced_positions'], ['A'])
        self.assertTrue(arm['equity_history'][-1]['equity_is_estimate'])
        self.assertEqual(arm['trades'], 0)

    def test_add_loss_and_sampled_drawdown(self):
        doc = fixture()
        doc['observations'][1]['market']['A'] = quote(98, T+300, add_eligible=True)
        doc['observations'].append(dict(time=T+600, market={'A': quote(90, T+600)}))
        arm = replay.replay_fixture(doc)['accounts']['baseline']
        self.assertEqual(arm['adds'], 1)
        self.assertEqual(arm['losses'], 1)
        self.assertGreater(arm['sampled_max_drawdown'], 0)
        self.assertEqual(sum(arm['close_reasons'].values()), 1)
        self.assertLess(arm['net_equity_change'], 0)

    def test_explicit_override_changes_provenance_without_mutation(self):
        doc = fixture()
        before = deepcopy(doc)
        base = replay.replay_fixture(doc)
        changed = replay.replay_fixture(doc, {'target_net_profit': 100.0})
        self.assertNotEqual(base['replay_id'], changed['replay_id'])
        self.assertNotEqual(base['provenance']['config_hash'], changed['provenance']['config_hash'])
        self.assertEqual(changed['accounts']['baseline']['entries'], 0)
        self.assertEqual(doc, before)
        with self.assertRaises(ValueError):
            replay.replay_fixture(doc, {'unknown': 1})

    def test_buy_rejection_evidence_is_not_a_fill(self):
        doc = fixture()
        for row in doc['observations']:
            row['market']['A']['ask_size'] = 1
        arm = replay.replay_fixture(doc)['accounts']['baseline']
        self.assertEqual(arm['entries'], 0)
        self.assertEqual(arm['execution_fees'], 0)
        self.assertEqual(arm['buy_rejections'], 2)
        self.assertEqual(arm['buy_rejection_reasons'], {'insufficient_ask_depth': 2})
        self.assertTrue(all(e['type'] == 'buy_rejected' for e in arm['events']))

    def test_invalid_checkpoint_mode_timestamp_nan_and_size(self):
        for mutate in (
            lambda d: d.pop('initial_state'),
            lambda d: d.update(mode='live'),
            lambda d: d['initial_state'].update(mode='live'),
            lambda d: d['initial_state'].update(last_run=T+1),
            lambda d: d['config'].pop('fee_rate'),
            lambda d: d['observations'][0].update(time=T-1),
            lambda d: d['observations'][0]['market']['A'].update(bid=float('nan')),
            lambda d: d.update(start_at=True),
        ):
            doc = fixture()
            mutate(doc)
            with self.assertRaises((ValueError, TypeError)):
                replay.replay_fixture(doc)

    def test_checkpoint_last_run_boundary_rejected(self):
        doc = fixture()
        doc['initial_state']['last_run'] = T
        with self.assertRaisesRegex(ValueError, 'follow'):
            replay.replay_fixture(doc)

    def test_unknown_checkpoint_fields_not_republished(self):
        doc = fixture()
        doc['initial_state']['private_note'] = 'sentinel-private-value'
        result = replay.replay_fixture(doc)
        self.assertNotIn('sentinel-private-value', json.dumps(result))

    def test_export_uses_explicit_checkpoint_never_current_account(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            runtime = root / 'runtime'
            runtime.mkdir()
            doc = fixture()
            checkpoint = root / 'checkpoint.json'
            checkpoint.write_text(json.dumps({k: v for k, v in doc.items() if k != 'observations'}))
            live = runtime / 'experiment.json'
            live.write_text(json.dumps(dict(mode='paper', accounts={'baseline': {'cash': 1}},
                                            observations=doc['observations'])))
            before = live.read_bytes()
            exported = replay.export_fixture(runtime, checkpoint)
            self.assertEqual(exported, doc)
            self.assertEqual(live.read_bytes(), before)
            self.assertEqual(exported['initial_state']['cash'], 1000)
            replay.write_output(root / 'result.json', exported, inputs=[checkpoint], runtime=runtime)
            self.assertEqual(json.loads((root / 'result.json').read_text()), doc)
            for output in (checkpoint, runtime / 'new.json', live, runtime, root):
                with self.assertRaises(ValueError):
                    replay.write_output(output, {}, inputs=[checkpoint], runtime=runtime)
            with self.assertRaises(FileExistsError):
                replay.write_output(root / 'result.json', exported)
            link = root / 'link'
            link.symlink_to(runtime, target_is_directory=True)
            with self.assertRaises(ValueError):
                replay.write_output(link / 'out.json', {})
            with self.assertRaises(ValueError):
                replay.export_fixture(link, checkpoint)

    def test_archive_merge_and_missing_archive_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            runtime = root / 'runtime'
            runtime.mkdir()
            archives = runtime / 'experiment-archive'
            archives.mkdir()
            doc = fixture()
            checkpoint = root / 'checkpoint.json'
            checkpoint.write_text(json.dumps({k: v for k, v in doc.items() if k != 'observations'}))
            live = dict(mode='paper', observations=[doc['observations'][-1]],
                        archive_files=['2027-01-15.json'])
            (runtime / 'experiment.json').write_text(json.dumps(live))
            with self.assertRaises(ValueError):
                replay.export_fixture(runtime, checkpoint)
            (archives / '2027-01-15.json').write_text(json.dumps(dict(schema=1, day='2027-01-15',
                                                       observations=[doc['observations'][0]])))
            exported = replay.export_fixture(runtime, checkpoint)
            self.assertEqual(replay.replay_fixture(exported)['accounts'],
                             replay.replay_fixture(doc)['accounts'])

    def test_cli_missing_checkpoint_and_nan_fail_closed(self):
        self.assertEqual(replay.main([]), 2)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory).resolve() / 'bad.json'
            path.write_text('{"value":NaN}')
            self.assertEqual(replay.main(['--input', str(path)]), 2)


if __name__ == '__main__':
    unittest.main()
