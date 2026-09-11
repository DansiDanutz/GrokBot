"""Account clarity and bounded recent history for the public paper page."""
from pathlib import Path
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
