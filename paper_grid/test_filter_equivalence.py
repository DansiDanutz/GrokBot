"""Audit eligibility must use exactly the same filter as paper execution."""
import copy
import unittest
from unittest.mock import patch

from paper_grid import audits, coinglass

NOW = 1789084800 + 180
SYMBOL = 'TESTUSDTM'


class FilterEquivalenceTests(unittest.TestCase):
    def features(self, *, cache_age=0, hour_age=180, **changes):
        feature = dict(latest_hour=NOW-coinglass.HOUR-hour_age, burst_ratio=3,
                       long_share=.60, total_usd=100, eligible=True)
        feature.update(changes)
        return dict(fetched_at=NOW-cache_age, symbols={SYMBOL: feature})

    def assert_audit_equivalent(self, features, *, base_eligible=True):
        quote = dict(symbol=SYMBOL, eligible=base_eligible, add_eligible=True,
                     quote_time=NOW, reasons=[])
        observation = dict(time=NOW, market={SYMBOL: quote}, coinglass=features)
        document = dict(observations=[observation], config={}, tick_seconds=300)
        before = copy.deepcopy(document)
        execution = coinglass.apply_filter({SYMBOL: quote}, features, NOW)[SYMBOL]
        data = audits._data_metrics(document, NOW-1, NOW+1)
        counts = data['coins'][SYMBOL]
        self.assertEqual(counts['filtered_signal_eligible'], int(execution['eligible']))
        missing = 'coinglass_missing_or_stale' in execution['reasons']
        self.assertEqual(counts['missing_or_stale_coinglass'], int(missing))
        expected = {reason: 1 for reason in execution['reasons'] if reason.startswith('coinglass_')}
        self.assertEqual(data['additional_filter_rejections'], expected)
        self.assertEqual(document, before)

    def test_real_thresholds_and_invalid_feature_boundaries(self):
        cases = [None, {}, self.features(), self.features(eligible=False)]
        for field, values in (
                ('cache_age', (-1, coinglass.CACHE_SECONDS-1, coinglass.CACHE_SECONDS)),
                ('hour_age', (-1, 0, coinglass.MAX_HISTORY_AGE, coinglass.MAX_HISTORY_AGE+1)),
                ('burst_ratio', (2.999, 3, 3.001, float('nan'), True)),
                ('long_share', (.599, .60, 1, 1.001)),
                ('total_usd', (0, 1, float('inf')))):
            cases.extend(self.features(**{field: value}) for value in values)
        for index, features in enumerate(cases):
            for eligible in (True, False):
                with self.subTest(case=index, eligible=eligible):
                    self.assert_audit_equivalent(features, base_eligible=eligible)

    def test_audit_follows_shared_freshness_parameters(self):
        with patch.object(coinglass, 'CACHE_SECONDS', 17), \
             patch.object(coinglass, 'MAX_HISTORY_AGE', 31):
            for cache_age, hour_age in ((16, 31), (17, 31), (16, 32)):
                with self.subTest(cache_age=cache_age, hour_age=hour_age):
                    self.assert_audit_equivalent(self.features(cache_age=cache_age, hour_age=hour_age))


if __name__ == '__main__':
    unittest.main()
