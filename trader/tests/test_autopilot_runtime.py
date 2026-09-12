"""Daemon passes with an injected clock and public-client fixture only."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from trader.autopilot.runtime import Runner, guarded_paths
from trader.autopilot.storage import read_json, atomic_json, read_events
from trader.data.store import Store
from trader.tests.test_autopilot_policy import radar, row

NOW = 1789142400000


class Client:
    def __init__(self, clock):
        self.clock, self.calls, self.price, self.fail = clock, 0, 100., False
    def all_tickers(self):
        self.calls += 1
        if self.fail:
            raise ValueError('provider secret must not escape')
        return [dict(symbol=s, ts_ms=self.clock[0], price=self.price)
                for s in ('XBTUSDTM', 'ETHUSDTM', 'SOLUSDTM', 'A')]
    def klines(self, *args):
        return []


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.database = self.root / 'market.sqlite3'
        with Store(self.database):
            pass
        self.clock = [NOW]
        self.client = Client(self.clock)
        self.radar = self.root / 'radar.json'
        report = radar(neutral=[row('A', 'NEUTRAL')])
        report.update(schema_version=1, asof_ms=NOW)
        atomic_json(self.radar, report)
        self.state = self.root / 'autopilot' / 'state.json'
        self.snapshot = self.root / 'autopilot.json'
    def runner(self, **kwargs):
        return Runner(self.database, self.state, self.radar, self.snapshot,
                      client=self.client, now_ms=lambda: self.clock[0], **kwargs)

    def test_once_tick_fill_and_snapshot_health(self):
        runner = self.runner()
        runner.pass_once()
        self.assertEqual(self.client.calls, 1)
        self.assertEqual(len(runner.state['open_bots']), 1)
        self.clock[0] += 10000
        self.client.price = 95.
        view = runner.pass_once()
        self.assertGreater(view['open_bots'][0]['fills'], 0)
        self.assertTrue(view['kucoin_ok'])
        self.assertEqual(view['tick_age_s'], 0)
        self.assertEqual(view['heartbeat_ms'], self.clock[0])
        self.assertEqual(read_json(self.snapshot)['equity'], view['equity'])
        self.assertEqual(self.state.stat().st_mode & 0o777, 0o600)

    def test_tick_age_follows_the_fetch_not_the_thinnest_trade(self):
        runner = self.runner(); runner.pass_once()
        self.clock[0] += 10000
        stale_trade = self.clock[0] - 600000
        original = self.client.all_tickers
        self.client.all_tickers = lambda: [dict(r, ts_ms=stale_trade) for r in original()]
        view = runner.pass_once()
        self.assertTrue(view['kucoin_ok'])
        self.assertEqual(view['tick_age_s'], 0)
        self.assertEqual(runner.state['runtime']['last_tick_ms'], self.clock[0])

    def test_radar_mtime_triggers_decision_and_cooldown(self):
        runner = self.runner()
        runner.pass_once()
        self.clock[0] += 10000
        report = radar(long=[row('A', 'LONG')]); report.update(schema_version=1, asof_ms=self.clock[0])
        atomic_json(self.radar, report)
        view = runner.pass_once()
        self.assertEqual(len(view['open_bots']), 0)
        self.assertEqual(view['closed_bots'][0]['reason'], 'LABEL_FLIP')

    def test_failed_market_logs_sanitized_error_and_recovers(self):
        runner = self.runner(); runner.pass_once()
        self.client.fail = True
        self.clock[0] += 181000
        view = runner.pass_once()
        self.assertFalse(view['kucoin_ok'])
        self.assertGreater(view['tick_age_s'], 180)
        self.client.fail = False; self.clock[0] += 10000
        runner.pass_once()
        events = read_events(self.state.parent, 0)
        self.assertTrue({'ERROR', 'ALERT', 'RECOVER'} <= {e['type'] for e in events})
        self.assertNotIn('provider secret', self.state.read_text())

    def test_local_policy_error_keeps_kucoin_ok_and_logs_code_5(self):
        runner = self.runner(); runner.pass_once()
        self.clock[0] += 10000
        def broken(updates):
            raise RuntimeError('local accounting bug')
        runner._apply = broken
        view = runner.pass_once()
        self.assertTrue(view['kucoin_ok'])
        self.assertEqual(view['tick_age_s'], 0)
        events = read_events(self.state.parent, 0)
        self.assertIn(5, {e.get('code') for e in events if e['type'] == 'ERROR'})
        self.assertNotIn('local accounting bug', self.state.read_text())
        self.assertNotIn('local accounting bug', (self.state.parent / 'events.jsonl').read_text())

    def test_restart_backfill_and_duplicate_pass_do_not_refill(self):
        runner = self.runner(); runner.pass_once()
        with Store(self.database) as store:
            store.upsert('klines', [dict(symbol='A', interval='1m', time_ms=NOW,
                open=100., high=101., low=95., close=99., volume=1., turnover=100.)])
        self.clock[0] += 60000
        recovered = self.runner(); recovered.pass_once()
        fills = recovered.state['open_bots'][0]['engine']['fills']
        self.assertGreater(fills, 0)
        again = self.runner(); again.pass_once()
        self.assertEqual(again.state['open_bots'][0]['engine']['fills'], fills)

    def test_unchanged_pass_has_at_most_thirty_second_heartbeat_write(self):
        atomic_json(self.radar, dict(schema_version=1, asof_ms=NOW, rows=[], sections={}))
        runner = self.runner(); runner.pass_once()
        fixed = self.client.all_tickers()
        self.client.all_tickers = lambda: fixed
        stamp = read_json(self.snapshot)['generated_at_ms']
        self.clock[0] += 10000; runner.pass_once()
        self.assertEqual(read_json(self.snapshot)['generated_at_ms'], stamp)
        self.clock[0] += 20000; runner.pass_once()
        self.assertEqual(read_json(self.snapshot)['generated_at_ms'], self.clock[0])

    def test_notification_failure_retains_events_for_retry(self):
        notifier = unittest.mock.Mock(side_effect=RuntimeError('no token leak'))
        runner = self.runner(chat_id='1', telegram_state=self.root/'telegram.json', notifier=notifier)
        runner.pass_once()
        self.assertTrue(runner.state['pending_notifications'])
        def accepted(view, events, now, chat_id, path):
            atomic_json(path, {'sent': {f"{e['type']}:{e['bot_id']}:{e['ts_ms']}": now for e in events}})
        notifier.side_effect = accepted
        self.clock[0] += 10000; runner.pass_once()
        self.assertEqual(runner.state['pending_notifications'], [])

    def test_response_time_trade_is_not_discarded_as_future(self):
        old = self.client.all_tickers
        def responding():
            self.clock[0] += 2000
            return old()
        self.client.all_tickers = responding
        runner = self.runner()
        view = runner.pass_once()
        self.assertEqual(runner.state['runtime']['quotes']['XBTUSDTM']['ts_ms'], self.clock[0])
        self.assertEqual(view['heartbeat_ms'], self.clock[0])

    def test_parent_traversal_cannot_bypass_protected_paths(self):
        with patch('trader.autopilot.runtime.Path.home', return_value=self.root):
            path = self.root/'Sandbox/grokbot/../grokbot/zmarty-paper-runtime/state.json'
            with self.assertRaises(ValueError):
                guarded_paths(self.database, path, self.radar, self.snapshot)

    def test_missing_radar_does_not_block_latched_safety_close(self):
        runner = self.runner(); runner.pass_once()
        runner.state['open_bots'][0]['signals'] = ['RANGE_BREAK']
        self.radar.unlink()
        self.clock[0] += 300000
        view = runner.pass_once()
        self.assertEqual(view['open_bots'], [])
        self.assertEqual(view['closed_bots'][0]['reason'], 'RANGE_BREAK')

    def test_expired_watchlist_is_pruned_before_retry_without_new_scan(self):
        notifier = unittest.mock.Mock(side_effect=RuntimeError('offline'))
        runner = self.runner(chat_id='1', telegram_state=self.root/'telegram.json', notifier=notifier)
        runner.pass_once()
        self.assertEqual(len(runner.state['pending_watchlists']), 1)
        self.clock[0] += 31 * 86400000
        runner.pass_once()
        self.assertEqual(notifier.call_args.args[0]['pending_watchlists'], [])
        self.assertEqual(read_json(self.state)['pending_watchlists'], [])

    def test_failed_watchlist_send_retains_prior_scan_across_restart(self):
        notifier = unittest.mock.Mock(side_effect=RuntimeError('offline'))
        runner = self.runner(chat_id='1', telegram_state=self.root/'telegram.json', notifier=notifier)
        runner.pass_once()
        first = copy.deepcopy(runner.state['pending_watchlists'][0])
        self.clock[0] += 3600000
        changed = row('A', 'NEUTRAL', expected_grids_per_hour=20)
        atomic_json(self.radar, dict(radar(neutral=[changed]), schema_version=1, asof_ms=self.clock[0]))
        runner.pass_once()
        self.assertEqual(len(runner.state['pending_watchlists']), 2)
        again = self.runner(chat_id='1', telegram_state=self.root/'telegram.json', notifier=notifier)
        self.assertEqual(again.state['pending_watchlists'][0], first)
        again.pass_once()
        self.assertEqual(len(again.state['pending_watchlists']), 2)
        def accepted(view, events, now, chat_id, path):
            oldest = view['pending_watchlists'][0]
            atomic_json(path, {'sent': {f"WATCHLIST:{oldest['watchlist_scan_id']}": now}})
        notifier.side_effect = accepted
        self.clock[0] += 10000; again.pass_once()
        self.assertEqual(len(again.state['pending_watchlists']), 1)
        self.assertEqual(again.state['pending_watchlists'][0]['watchlist_scan_id'], str(NOW+3600000))

    def test_watchlist_swap_survives_restart_without_repeating_events(self):
        rows = [row(f'N{i}', 'NEUTRAL') for i in range(5)]
        report = dict(radar(neutral=rows), schema_version=1, asof_ms=NOW)
        atomic_json(self.radar, report)
        runner = self.runner(); runner.pass_once()
        self.clock[0] += 2 * 3600000
        rows.append(row('NEW', 'NEUTRAL', expected_grids_per_hour=20,
                        turnover_24h_usdt=30_000_000))
        atomic_json(self.radar, dict(radar(neutral=rows), schema_version=1, asof_ms=self.clock[0]))
        view = runner.pass_once()
        self.assertIn('NEW', {r['symbol'] for r in view['watchlist']['core']})
        self.assertEqual(len(view['watchlist_history']), 2)
        history = read_events(self.state.parent, 0)
        self.assertEqual(sum(e['type'] == 'PROMOTE' for e in history), 1)
        again = self.runner(); restored = again.pass_once()
        self.assertEqual(restored['watchlist_history'], view['watchlist_history'])
        self.assertEqual(restored['watchlist'], view['watchlist'])
        after = read_events(self.state.parent, 0)
        self.assertEqual([event for event in after if event['type'] != 'DECISION'],
                         [event for event in history if event['type'] != 'DECISION'])
        self.assertTrue(all(event['type'] == 'DECISION'
                            for event in after[len(history):]))

    def test_stale_radar_cannot_open_core_bots(self):
        self.clock[0] += 121 * 60000
        runner = self.runner(); view = runner.pass_once()
        self.assertEqual(view['watchlist']['core'], [])
        self.assertEqual(view['open_bots'], [])

    def test_previous_phase_b_state_migrates_watchlist_without_closing(self):
        runner = self.runner(); runner.pass_once()
        previous = read_json(self.state)
        del previous['watchlist']
        atomic_json(self.state, previous)
        again = self.runner(); view = again.pass_once()
        self.assertEqual([r['symbol'] for r in view['watchlist']['core']], ['A'])
        self.assertEqual(len(view['open_bots']), 1)
        self.assertEqual(view['closed_bots'], [])

    def test_each_missed_funding_boundary_uses_its_stored_rate_once(self):
        atomic_json(self.radar, dict(radar(long=[row('A', 'LONG')]), schema_version=1, asof_ms=NOW))
        runner = self.runner(); runner.pass_once()
        bot = runner.state['open_bots'][0]['engine']
        quantity = bot['position_contracts']
        boundary = (NOW // 28800000 + 1) * 28800000
        with Store(self.database) as store:
            store.upsert('funding', [dict(symbol='A', time_ms=at, rate=rate, period_ms=28800000)
                         for at, rate in ((boundary, .001), (boundary+28800000, -.002))])
        update = {'A': dict(ts_ms=boundary+28800000, price=100.)}
        bot['risk_metadata_at_ms'] = update['A']['ts_ms']  # Isolate settlement-rate test from stale-risk exit.
        runner._apply(update)
        result = runner.state['open_bots'][0]['engine']
        self.assertAlmostEqual(result['funding_paid'], quantity * 100 * (.001-.002))
        before = copy.deepcopy(result)
        runner._apply(update)
        self.assertEqual(runner.state['open_bots'][0]['engine'], before)


if __name__ == '__main__':
    unittest.main()
