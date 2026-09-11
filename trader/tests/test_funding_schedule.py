"""Contract-specific funding evidence, never an assumed eight-hour clock."""
import json
from pathlib import Path
import tempfile
import unittest
from trader.data.store import Store
from trader.autopilot.funding import settlements, charge

HOUR = 3600000

class FundingScheduleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name).resolve() / 'market.sqlite3'
        with Store(self.db): pass

    def snapshot(self, observed, next_at, interval=4*HOUR, rate=.001, **risk):
        record = dict(symbol='RAYUSDTM', time_ms=observed, observed_at_ms=observed,
                      source_time_ms=observed, last=100., mark_price=100., index_price=100.,
                      volume_24h=0., turnover_24h=0., open_interest=0., funding_rate=rate,
                      raw_json=json.dumps(dict(fundingRateGranularity=interval,
                                               nextFundingRateDateTime=next_at, **risk)))
        with Store(self.db) as store: store.upsert('ticker_snapshots', [record])

    def bot(self, quantity=10):
        return dict(symbol='RAYUSDTM', opened_ms=HOUR, last_ts_ms=HOUR,
                    last_funding_ts_ms=0, position_contracts=quantity, last_price=100.,
                    funding_paid=0.)

    def test_four_hour_contract_uses_next_date_not_eight_hour_clock(self):
        self.snapshot(3*HOUR, 4*HOUR)
        bot = self.bot(); charge(self.db, bot, 4*HOUR)
        self.assertEqual(bot['funding_paid'], 1.)
        self.assertEqual(bot['funding_interval_ms'], 4*HOUR)
        self.assertTrue(bot['funding_managed'])
        charge(self.db, bot, 4*HOUR)
        self.assertEqual(bot['funding_paid'], 1.)

    def test_negative_position_receives_positive_rate(self):
        self.snapshot(3*HOUR, 4*HOUR)
        bot = self.bot(-10); charge(self.db, bot, 4*HOUR)
        self.assertEqual(bot['funding_paid'], -1.)

    def test_exact_historical_rates_override_estimates_and_do_not_carry(self):
        self.snapshot(3*HOUR, 4*HOUR)
        with Store(self.db) as store:
            store.upsert('funding', [dict(symbol='RAYUSDTM', time_ms=4*HOUR,
                                          rate=.002, period_ms=4*HOUR)])
        values, _ = settlements(self.db, 'RAYUSDTM', HOUR, 12*HOUR)
        self.assertEqual([(r['ts_ms'], r['rate']) for r in values], [(4*HOUR, .002)])

    def test_stale_and_future_metadata_cannot_invent_settlements(self):
        self.snapshot(HOUR, 4*HOUR)
        self.snapshot(5*HOUR, 4*HOUR)
        values, _ = settlements(self.db, 'RAYUSDTM', HOUR, 4*HOUR)
        self.assertEqual(values, [])

    def test_separate_intervals_and_non_utc_aligned_boundary(self):
        self.snapshot(4*HOUR, 5*HOUR, 8*HOUR)
        bot = self.bot(); charge(self.db, bot, 5*HOUR)
        self.assertEqual(bot['funding_paid'], 1.)
        self.assertEqual(bot['funding_interval_ms'], 8*HOUR)

    def test_restart_preserves_cursor_and_never_charges_same_boundary_twice(self):
        self.snapshot(3*HOUR, 4*HOUR)
        bot = self.bot(); charge(self.db, bot, 4*HOUR)
        restored = json.loads(json.dumps(bot))
        charge(self.db, restored, 4*HOUR+10000)
        self.assertEqual(restored['funding_paid'], 1.)

    def test_new_rate_is_not_applied_to_an_earlier_missing_boundary(self):
        self.snapshot(7*HOUR, 8*HOUR, rate=.003)
        values, _ = settlements(self.db, 'RAYUSDTM', HOUR, 8*HOUR)
        self.assertEqual([(r['ts_ms'], r['rate']) for r in values], [(8*HOUR, .003)])

    def test_fresh_risk_metadata_refreshes_tier_and_observed_timestamp(self):
        self.snapshot(3*HOUR, 4*HOUR, maintainMargin=.01, minRiskLimit=10000.)
        bot = self.bot(); charge(self.db, bot, 4*HOUR)
        self.assertEqual(bot['maintain_margin'], .01)
        self.assertEqual(bot['risk_limit'], 10000.)
        self.assertEqual(bot['risk_metadata_at_ms'], 3*HOUR)

    def test_invalid_risk_metadata_cannot_refresh_existing_evidence(self):
        self.snapshot(3*HOUR, 4*HOUR, maintainMargin=True, minRiskLimit=10000.)
        bot = dict(self.bot(), maintain_margin=.01, risk_limit=5000., risk_metadata_at_ms=HOUR)
        charge(self.db, bot, 4*HOUR)
        self.assertEqual(bot['risk_metadata_at_ms'], HOUR)
        self.assertEqual(bot['risk_limit'], 5000.)

    def test_missing_evidence_never_uses_bot_start_rate(self):
        bot = dict(self.bot(), funding_pct=10.)
        charge(self.db, bot, 24*HOUR)
        self.assertEqual(bot['funding_paid'], 0.)
        self.assertEqual(bot['funding_schedule_status'], 'UNAVAILABLE')

if __name__ == '__main__': unittest.main()
