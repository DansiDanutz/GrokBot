"""Range provenance, completed-candle inputs, and new futures-bot fee defaults."""
import unittest
from copy import deepcopy
from contextlib import closing
from trader.radar import support
from trader.radar.radar import _aggregate, _latest
from trader.radar.spacing import economics, choose_count
from trader.radar.layout import select_range, layout_valid
import sqlite3
from trader.papergrid import engine

HOUR = 3600000
NOW = 200 * HOUR


def candles():
    return [(NOW-(168-i)*HOUR, 100, 110 if i % 8 == 5 else 104,
             90 if i % 8 == 2 else 96, 100, 1) for i in range(168)]


class RangeAssuranceTests(unittest.TestCase):
    def test_range_evidence_names_completed_pivots_and_window(self):
        evidence = support.assess(candles(), 100, 2, NOW)
        self.assertEqual(evidence['status'], 'VERIFIED')
        self.assertEqual(evidence['coverage_ratio'], 1)
        self.assertEqual(evidence['candle_asof_ms'], NOW)
        self.assertEqual(evidence['window_start_ms'], NOW-168*HOUR)
        self.assertEqual(len(evidence['candles_sha256']), 64)
        pivot = evidence['supports'][0]
        self.assertGreaterEqual(pivot['touches'], 2)
        self.assertEqual(pivot['confirmed_at_ms'][0], pivot['pivot_times_ms'][0]+3*HOUR)

    def test_missing_latest_or_interior_hour_rejects_structure(self):
        for rows in [candles()[:-1], candles()[:80]+candles()[81:]]:
            result = support.assess(rows, 100, 2, NOW)
            self.assertEqual(result['status'], 'REJECTED')
            self.assertFalse(result['contiguous'])
            self.assertEqual(result['supports'], [])
            self.assertEqual(result['resistances'], [])

    def test_partial_and_gappy_higher_timeframes_are_excluded(self):
        rows = [(i*HOUR,100,101,99,100,1) for i in range(7)]
        self.assertEqual(len(_aggregate(rows, 4*HOUR, 7*HOUR)), 1)
        self.assertEqual(_aggregate(rows[:2]+rows[3:], 4*HOUR, 7*HOUR), [])

    def test_maximum_count_uses_actual_last_grid_line(self):
        count = choose_count(100,106.53,tick_size=.01)
        self.assertEqual(count,19)
        result=economics(100,106.53,count,tick_size=.01)
        self.assertGreater(result['profit_pct_min'],1)
        self.assertAlmostEqual(result['actual_upper_line'],106.46)
        self.assertFalse(economics(100,106.53,count+1,tick_size=.01)['viable'])

    def test_new_bot_defaults_and_saved_fee_rates_remain_distinct(self):
        spec=dict(bot_id=1,symbol='TEST',direction='LONG',range_low=98,range_high=104,
                  grids=12,grid_interval=.5,step_pct=.5,notional_usdt=1000,
                  leverage=5,funding_pct=0,accounting_version=2,quantity_per_grid=1)
        new=engine.open_bot(spec,100,0)
        self.assertEqual(new['fee_rate_maker'],.0006)
        self.assertEqual(new['fee_rate_taker'],.0006)
        legacy=engine.open_bot(dict(spec,fee_rate_maker=.0002),100,0)
        original=deepcopy(legacy)
        updated,events=engine.step(legacy,dict(price=101,ts_ms=1000))
        fills=[e for e in events if e['type']=='FILL']
        self.assertTrue(fills)
        self.assertAlmostEqual(fills[0]['fee'], fills[0]['contracts']*fills[0]['price']*.0002)
        self.assertEqual(legacy,original)
        self.assertEqual(updated['fee_rate_maker'],.0002)

    def test_existing_bot_without_stored_fee_fields_keeps_historical_fallback(self):
        spec=dict(bot_id=1,symbol='TEST',direction='LONG',range_low=98,range_high=104,
                  grids=12,grid_interval=.5,step_pct=.5,notional_usdt=1000,
                  leverage=5,funding_pct=0,accounting_version=2,quantity_per_grid=1)
        legacy=engine.open_bot(spec,100,0)
        del legacy['fee_rate_maker']
        del legacy['fee_rate_taker']
        updated,events=engine.step(legacy,dict(price=101,ts_ms=1000))
        fill=next(e for e in events if e['type']=='FILL')
        self.assertAlmostEqual(fill['fee'],fill['contracts']*fill['price']*.0002)
        self.assertNotIn('fee_rate_maker',updated)

    def test_selected_range_preserves_original_and_rounded_provenance(self):
        rows=[(r[0],r[1],r[2]+.005 if r[2]==110 else r[2],
               r[3]+.005 if r[3]==90 else r[3],r[4],r[5]) for r in candles()]
        basis=support.assess(rows,100,2,NOW)
        row=dict(symbol='TEST',price=100,tick_size=.01,maintain_margin=.005,
                 risk_limit=1000000,multiplier=.001,lot_size=1)
        selected,reason=select_range([(p['price'],p['touches']) for p in basis['supports']],
                                    [(p['price'],p['touches']) for p in basis['resistances']],
                                    row,'NEUTRAL',evidence=basis)
        self.assertEqual(reason,'')
        proof=selected['range_evidence']
        self.assertEqual(proof['original_bounds'],[90.005,110.005])
        self.assertEqual(proof['rounded_bounds'],[90.01,110.0])
        self.assertEqual(proof['status'],'VERIFIED')
        self.assertEqual(proof['fee_rate_maker'],.0006)
        self.assertEqual(proof['fee_rate_taker'],.0006)
        self.assertTrue(proof['selected_support']['pivot_times_ms'])
        original=deepcopy(proof)
        basis['supports'][0]['pivot_times_ms'].clear()
        self.assertEqual(proof,original)
        for n in range(selected['grids']+1,201):
            spacing=economics(90.01,110,n,tick_size=.01,direction='NEUTRAL')
            self.assertFalse(spacing['viable'] and layout_valid(90.01,spacing['interval'],n,100,'NEUTRAL'))

    def test_historical_snapshot_lookup_does_not_read_future_quote(self):
        with closing(sqlite3.connect(':memory:')) as db:
            db.execute('CREATE TABLE samples(symbol TEXT,time_ms INTEGER,price REAL)')
            db.executemany('INSERT INTO samples VALUES (?,?,?)',[('TEST',10,100),('TEST',30,999)])
            self.assertEqual(_latest(db,'samples',('time_ms','price'),20),{'TEST':(10,100)})

    def test_initial_seed_and_resting_pairs_use_same_fee_floor(self):
        count=choose_count(100,106.53,tick_size=.01)
        e=economics(100,106.53,count,tick_size=.01)
        entry=100+8*e['interval']
        spec=dict(bot_id=1,symbol='TEST',direction='LONG',range_low=100,range_high=106.53,
                  grids=count,grid_interval=e['interval'],step_pct=e['interval']/entry*100,
                  notional_usdt=1000,leverage=5,funding_pct=0,accounting_version=2,quantity_per_grid=1)
        bot=engine.open_bot(spec,entry,0)
        updated,events=engine.step(bot,dict(price=entry+e['interval'],ts_ms=1000))
        fill=next(e for e in events if e['type']=='FILL')
        pair_net=fill['price']-entry-entry*bot['fee_rate_taker']-fill['fee']
        self.assertGreater(pair_net/entry*500,1)
        self.assertEqual(updated['completed_grids'],1)


if __name__ == '__main__':
    unittest.main()
