"""Account clarity and bounded recent history for the public paper page."""
from pathlib import Path
import json
import tempfile
import unittest
from trader.autopilot.storage import EventLog, read_events
from paper_grid.public_autopilot import safe


class PositionVisibilityTests(unittest.TestCase):
    def test_balance_separates_floating_pnl_and_allocated_capital(self):
        source = dict(schema_version=1, equity=9980, open_bots=[dict(bot_id=1,
            symbol='RAYUSDTM', direction='LONG', notional_usdt=1000, reserve_usdt=200,
            unrealized_pnl=-30)], closed_bots=[], groups={}, totals=dict(
            fees=5, funding=2), equity_curve=[])
        result = safe(source)['account']
        self.assertEqual(result['starting_equity'], 10000)
        self.assertEqual(result['balance'], 10010)
        self.assertEqual(result['equity'], 9980)
        self.assertEqual(result['allocated_margin'], 1000)
        self.assertEqual(result['reserved_margin'], 200)
        self.assertEqual(result['free_balance'], 8810)
        self.assertEqual(result['realized_pnl'], 10)
        self.assertEqual(result['unrealized_pnl'], -30)

    def test_grid_sizing_and_current_empty_level_are_public(self):
        source = dict(schema_version=1, equity=10000, open_bots=[dict(bot_id=1,
            symbol='RAYUSDTM', direction='NEUTRAL', contracts_per_line=17,
            empty_line=32)], closed_bots=[], groups={}, totals={})
        result = safe(source)['open_bots'][0]
        self.assertEqual(result['contracts_per_line'], 17)
        self.assertEqual(result['empty_line'], 32)

    def test_recent_events_are_latest_not_first_and_keep_existing_pagination(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder).resolve();log=EventLog(path)
            log.append([dict(ts_ms=1000,event_id=i,bot_id=1,symbol='RAYUSDTM',type='FILL',price=1.5)
                        for i in range(1,601)])
            self.assertEqual([e['event_id'] for e in read_events(path,0,limit=50,latest=True)],list(range(551,601)))
            self.assertEqual(read_events(path,0,limit=50)[0]['event_id'],1)

    def test_liquidation_estimate_is_allowlisted(self):
        source = dict(schema_version=1, equity=10000, open_bots=[dict(bot_id=1,
            symbol='RAYUSDTM', direction='LONG', liquidation=dict(status='ESTIMATED',
            price=1.2, with_reserve_price=1.1, mmr=.005, fee_rate=.0006,
            metadata_at_ms=1000, private_note='omit'))], closed_bots=[], groups={}, totals={})
        risk = safe(source)['open_bots'][0]['liquidation']
        self.assertEqual(risk['price'], 1.2)
        self.assertNotIn('private_note', risk)

    def test_review_status_is_allowlisted_with_closed_vocabulary(self):
        status = dict(last_run_at_ms=1789160400000, doctrine_version='2026-09-12T07:00:04',
                      proposals=dict(planned=3, applied=1, deferred=2, rejected=0),
                      active_rules=dict(min_hold_hours_before_non_risk_close=4,
                                        require_trend_alignment=True,
                                        symbol_cooldowns=['NEARUSDTM']),
                      evidence_headline='min_hold=4h from premature close(s) ($48.05 missed)',
                      extra_field='dropped')
        source = dict(schema_version=1, equity=10000, open_bots=[], closed_bots=[],
                      groups={}, totals={}, review_status=status)
        result = safe(source)['review_status']
        self.assertEqual(result['proposals'], dict(planned=3, applied=1, deferred=2, rejected=0))
        self.assertEqual(result['active_rules']['symbol_cooldowns'], ['NEARUSDTM'])
        self.assertNotIn('extra_field', result)

    def test_review_status_rejects_bad_shapes(self):
        base = dict(schema_version=1, equity=10000, open_bots=[], closed_bots=[],
                    groups={}, totals={})
        good = dict(last_run_at_ms=1789160400000, proposals=dict(planned=0, applied=0,
                     deferred=0, rejected=0), active_rules=dict(
                     min_hold_hours_before_non_risk_close=None,
                     require_trend_alignment=False, symbol_cooldowns=[]),
                     evidence_headline='none')
        for mutate in (
                lambda s: s.pop('evidence_headline'),
                lambda s: s.update(evidence_headline='x' * 300),
                lambda s: s['proposals'].update(applied=-1),
                lambda s: s['active_rules'].update(require_trend_alignment='yes'),
                lambda s: s['active_rules'].update(min_hold_hours_before_non_risk_close=-2),
                lambda s: s['active_rules'].update(symbol_cooldowns=['not-a-symbol!']),
        ):
            bad = json.loads(json.dumps(good))
            mutate(bad)
            with self.assertRaises(ValueError, msg=str(bad)):
                safe(dict(base, review_status=bad))
        # absent review_status stays absent, never raises
        self.assertNotIn('review_status', safe(base))
