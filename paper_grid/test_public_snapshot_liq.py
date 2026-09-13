"""The published radar must carry the liquidation levels the engine computes.

On 2026-09-13 the Technical Interpreter reported clusters as
UNAVAILABLE_NOT_WIRED. It was right: every local row carried liq_* fields and
the publisher's allowlist dropped all four, so the only file the review team can
read showed nothing. A signal advisors cannot see is one they cannot use.
"""
import unittest

from paper_grid import public_snapshot


def radar(**over):
    row = dict(symbol='DOTUSDTM', direction='NEUTRAL', passes_liquidity=True,
               price=1.0, turnover_24h_usdt=9e6, spread_pct=.02, snapshot_age_min=3.0,
               funding_pct=.01, listing_age_days=900.0, atr_1h_pct=1.0, atr_4h_pct=2.0,
               slope_4h_pct=.1, position_7d=.5, change_24h_pct=1.0, low_7d=.9, high_7d=1.1,
               range_low=.95, range_high=1.05, step_pct=.45, grids=40,
               expected_grids_per_hour=4.0, rank_score=3.0, grid_interval=.0025,
               profit_pct_min=1.1, profit_pct_max=1.4, tick_size=.0001,
               liq_below_pct=1.25, liq_below_usd=3969.0,
               liq_above_pct=1.5, liq_above_usd=2455.0)
    row.update(over)
    return dict(schema_version=1, generated_at_ms=1789330000000, asof_ms=1789329000000,
                liq_clusters_generated_at_ms=1789330335675,
                sections=dict(neutral=[row]))


class PublishedLiquidationLevels(unittest.TestCase):
    def published_row(self, **over):
        out = public_snapshot._radar(radar(**over))
        return out, out['sections']['neutral'][0]

    def test_all_four_levels_reach_the_published_file(self):
        _, row = self.published_row()
        self.assertEqual(row['liq_below_pct'], 1.25)
        self.assertEqual(row['liq_below_usd'], 3969.0)
        self.assertEqual(row['liq_above_pct'], 1.5)
        self.assertEqual(row['liq_above_usd'], 2455.0)

    def test_the_cluster_timestamp_is_published_so_staleness_is_visible(self):
        out, _ = self.published_row()
        self.assertEqual(out['liq_clusters_generated_at_ms'], 1789330335675)

    def test_a_row_without_levels_still_publishes(self):
        """The annotation is advisory; an un-annotated scan must not fail."""
        source = radar()
        for key in ('liq_below_pct', 'liq_below_usd', 'liq_above_pct', 'liq_above_usd'):
            source['sections']['neutral'][0].pop(key)
        source.pop('liq_clusters_generated_at_ms')
        out = public_snapshot._radar(source)
        self.assertEqual(out['sections']['neutral'][0]['symbol'], 'DOTUSDTM')


if __name__ == '__main__':
    unittest.main()
