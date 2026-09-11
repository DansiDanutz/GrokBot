"""Windowed per-tick marks with no filesystem or provider access."""
from copy import deepcopy
import unittest
from paper_grid import audits


def document():
    return dict(start_at=0, accounts={'baseline': {'statistics': {'initial_equity': 1000}}},
                history=[], observations=[], events=[], tick_seconds=300, report_seconds=1800)


def sample(at, value, estimated=False, **fields):
    return dict(time=at, telemetry_schema=1,
                equity={'baseline': {'equity': value, 'equity_is_estimate': estimated}}, **fields)


class EquitySamplingTests(unittest.TestCase):
    def test_window_uses_boundary_then_five_minute_dip_not_outside_extremes(self):
        doc = document()
        doc['history'] = [dict(time=900, baseline_equity=1000), dict(time=1500, baseline_equity=1000)]
        doc['observations'] = [sample(600, 2000), sample(900, 1000), sample(1200, 990),
                               sample(1500, 1000), sample(1800, 500)]
        result = audits._arm_metrics(doc, 'baseline', 900, 1500)
        self.assertEqual(result['observed_max_drawdown'], 10)
        self.assertEqual(result['observed_max_drawdown_pct'], 1)
        self.assertEqual(result['equity_change'], 0)

    def test_duplicate_identical_tick_counts_one_estimated_sample(self):
        doc = document()
        row = sample(300, 995, True)
        doc['observations'] = [row, deepcopy(row)]
        before = deepcopy(doc)
        result = audits._arm_metrics(doc, 'baseline', 0, 600)
        self.assertEqual(result['equity_sampling']['tick_samples'], 1)
        self.assertEqual(result['equity_sampling']['estimated_tick_samples'], 1)
        self.assertEqual(doc, before)

    def test_conflicting_tick_valuations_fail_instead_of_silently_picking_one(self):
        doc = document()
        doc['observations'] = [sample(300, 990), sample(300, 980)]
        with self.assertRaisesRegex(ValueError, 'conflicting.*equity'):
            audits._arm_metrics(doc, 'baseline', 0, 600)

    def test_tick_mark_wins_publication_and_skipped_or_future_marks_do_not_count(self):
        doc = document()
        doc['history'] = [dict(time=300, baseline_equity=1000)]
        doc['observations'] = [sample(300, 990), sample(400, 0, skipped='cycle_error'),
                               sample(600, 995), sample(900, 100)]
        result = audits._arm_metrics(doc, 'baseline', 300, 600)
        self.assertEqual(result['start_mark'], {'time': 300, 'equity': 990})
        self.assertEqual(result['end_mark'], {'time': 600, 'equity': 995})
        self.assertEqual(result['equity_sampling']['tick_samples'], 2)
