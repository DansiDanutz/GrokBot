# Red-first evidence

These are intentional pre-implementation failures, not final gate results.

## core-red

```text
    module = __import__(module_name)
  File "/Users/davidai/ZCodeProject/GrokBot-grid-kucoin/trader/tests/test_kucoin_grid.py", line 2, in <module>
    from trader.strategies.kucoin_grid import create_bot, advance, stop, preview, net_equity
ModuleNotFoundError: No module named 'trader.strategies.kucoin_grid'


======================================================================
ERROR: test_grid_calibration (unittest.loader._FailedTest.test_grid_calibration)
----------------------------------------------------------------------
ImportError: Failed to import test module: test_grid_calibration
Traceback (most recent call last):
  File "/opt/homebrew/Cellar/python@3.14/3.14.7/Frameworks/Python.framework/Versions/3.14/lib/python3.14/unittest/loader.py", line 141, in loadTestsFromName
    module = __import__(module_name)
  File "/Users/davidai/ZCodeProject/GrokBot-grid-kucoin/trader/tests/test_grid_calibration.py", line 5, in <module>
    from trader.strategies.grid_calibration import calibration_report, fixture_config
ModuleNotFoundError: No module named 'trader.strategies.grid_calibration'


----------------------------------------------------------------------
Ran 2 tests in 0.000s

FAILED (errors=2)
```

## core-ray-red

```text

======================================================================
ERROR: test_ray_user_percentage_formula_and_actual_profit_are_separate (trader.tests.test_kucoin_grid.GridTests.test_ray_user_percentage_formula_and_actual_profit_are_separate)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/Users/davidai/ZCodeProject/GrokBot-grid-kucoin/trader/tests/test_kucoin_grid.py", line 314, in test_ray_user_percentage_formula_and_actual_profit_are_separate
    from trader.strategies.kucoin_grid import margin_profit_per_grid
ImportError: cannot import name 'margin_profit_per_grid' from 'trader.strategies.kucoin_grid' (/Users/davidai/ZCodeProject/GrokBot-grid-kucoin/trader/strategies/kucoin_grid.py)

======================================================================
FAIL: test_neutral_long_and_short_slots_survive_same_interval_roundtrip (trader.tests.test_kucoin_grid.GridTests.test_neutral_long_and_short_slots_survive_same_interval_roundtrip)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/Users/davidai/ZCodeProject/GrokBot-grid-kucoin/trader/tests/test_kucoin_grid.py", line 331, in test_neutral_long_and_short_slots_survive_same_interval_roundtrip
    self.assertEqual(len(state.orders), 40)
    ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^
AssertionError: 20 != 40

----------------------------------------------------------------------
Ran 39 tests in 0.013s

FAILED (failures=1, errors=2)
```

## core-ray-calibration-red

```text
.E.....E.
======================================================================
ERROR: test_ray_creation_separates_predicted_quantity_from_observed_lots (trader.tests.test_grid_calibration.CalibrationTests.test_ray_creation_separates_predicted_quantity_from_observed_lots)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/Users/davidai/ZCodeProject/GrokBot-grid-kucoin/trader/tests/test_grid_calibration.py", line 103, in test_ray_creation_separates_predicted_quantity_from_observed_lots
    from trader.strategies.grid_calibration import neutral_creation_calibration
ImportError: cannot import name 'neutral_creation_calibration' from 'trader.strategies.grid_calibration' (/Users/davidai/ZCodeProject/GrokBot-grid-kucoin/trader/strategies/grid_calibration.py)

======================================================================
ERROR: test_user_margin_formula_is_tested_honestly_against_four_longs (trader.tests.test_grid_calibration.CalibrationTests.test_user_margin_formula_is_tested_honestly_against_four_longs)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/Users/davidai/ZCodeProject/GrokBot-grid-kucoin/trader/tests/test_grid_calibration.py", line 97, in test_user_margin_formula_is_tested_honestly_against_four_longs
    self.assertAlmostEqual(row['margin_formula_profit_per_grid'], expected)
                           ~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
KeyError: 'margin_formula_profit_per_grid'

----------------------------------------------------------------------
Ran 9 tests in 0.013s

FAILED (errors=2)
```

## setup-red

```text
    module = __import__(module_name)
  File "/Users/davidai/ZCodeProject/GrokBot-grid-kucoin/trader/tests/test_grid_features.py", line 3, in <module>
    from trader.strategies.grid_features import features
ModuleNotFoundError: No module named 'trader.strategies.grid_features'


======================================================================
ERROR: test_grid_setup (unittest.loader._FailedTest.test_grid_setup)
----------------------------------------------------------------------
ImportError: Failed to import test module: test_grid_setup
Traceback (most recent call last):
  File "/opt/homebrew/Cellar/python@3.14/3.14.7/Frameworks/Python.framework/Versions/3.14/lib/python3.14/unittest/loader.py", line 141, in loadTestsFromName
    module = __import__(module_name)
  File "/Users/davidai/ZCodeProject/GrokBot-grid-kucoin/trader/tests/test_grid_setup.py", line 5, in <module>
    from trader.strategies.grid_setup import build_setup, setup
ModuleNotFoundError: No module named 'trader.strategies.grid_setup'


----------------------------------------------------------------------
Ran 2 tests in 0.000s

FAILED (errors=2)
```

## setup-ray-red

```text
E
======================================================================
ERROR: test_grid_setup (unittest.loader._FailedTest.test_grid_setup)
----------------------------------------------------------------------
ImportError: Failed to import test module: test_grid_setup
Traceback (most recent call last):
  File "/opt/homebrew/Cellar/python@3.14/3.14.7/Frameworks/Python.framework/Versions/3.14/lib/python3.14/unittest/loader.py", line 141, in loadTestsFromName
    module = __import__(module_name)
  File "/Users/davidai/ZCodeProject/GrokBot-grid-kucoin/trader/tests/test_grid_setup.py", line 5, in <module>
    from trader.strategies.grid_setup import build_setup, setup, grid_profit_preview
ImportError: cannot import name 'grid_profit_preview' from 'trader.strategies.grid_setup' (/Users/davidai/ZCodeProject/GrokBot-grid-kucoin/trader/strategies/grid_setup.py)


----------------------------------------------------------------------
Ran 1 test in 0.000s

FAILED (errors=1)
```

## pipeline-radar-replacement-red

```text
    from trader.research.kucoin_radar import (
        radar, normalize_market, crossing_score, compare_volatility, volatility_proxy,
    )
ModuleNotFoundError: No module named 'trader.research.kucoin_radar'


======================================================================
ERROR: test_kucoin_replacement (unittest.loader._FailedTest.test_kucoin_replacement)
----------------------------------------------------------------------
ImportError: Failed to import test module: test_kucoin_replacement
Traceback (most recent call last):
  File "/opt/homebrew/Cellar/python@3.14/3.14.7/Frameworks/Python.framework/Versions/3.14/lib/python3.14/unittest/loader.py", line 141, in loadTestsFromName
    module = __import__(module_name)
  File "/Users/davidai/ZCodeProject/GrokBot-grid-kucoin/trader/tests/test_kucoin_replacement.py", line 2, in <module>
    from trader.research.kucoin_replacement import decide_replacement
ModuleNotFoundError: No module named 'trader.research.kucoin_replacement'


----------------------------------------------------------------------
Ran 2 tests in 0.000s

FAILED (errors=2)
```

## pipeline-tracker-red

```text
E
======================================================================
ERROR: test_kucoin_tracker (unittest.loader._FailedTest.test_kucoin_tracker)
----------------------------------------------------------------------
ImportError: Failed to import test module: test_kucoin_tracker
Traceback (most recent call last):
  File "/opt/homebrew/Cellar/python@3.14/3.14.7/Frameworks/Python.framework/Versions/3.14/lib/python3.14/unittest/loader.py", line 141, in loadTestsFromName
    module = __import__(module_name)
  File "/Users/davidai/ZCodeProject/GrokBot-grid-kucoin/trader/tests/test_kucoin_tracker.py", line 5, in <module>
    from trader.research.kucoin_tracker import track_bars, track_summary
ModuleNotFoundError: No module named 'trader.research.kucoin_tracker'


----------------------------------------------------------------------
Ran 1 test in 0.000s

FAILED (errors=1)
```

## pipeline-edge-red

```text
======================================================================
FAIL: test_intrahour_emergency_survives_price_recovery (trader.tests.test_kucoin_tracker.TrackerTests.test_intrahour_emergency_survives_price_recovery)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/Users/davidai/ZCodeProject/GrokBot-grid-kucoin/trader/tests/test_kucoin_tracker.py", line 91, in test_intrahour_emergency_survives_price_recovery
    self.assertTrue(summary['emergency'])
    ~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^
AssertionError: False is not true

======================================================================
FAIL: test_closed_stop_loss_uses_actual_realized_close_cost (trader.tests.test_kucoin_replacement.ReplacementTests.test_closed_stop_loss_uses_actual_realized_close_cost)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/Users/davidai/ZCodeProject/GrokBot-grid-kucoin/trader/tests/test_kucoin_replacement.py", line 77, in test_closed_stop_loss_uses_actual_realized_close_cost
    self.assertEqual(cost['net_realized_on_close'], -47)
    ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
AssertionError: 0 != -47

----------------------------------------------------------------------
Ran 17 tests in 0.005s

FAILED (failures=2)
```

## pipeline-range-red

```text
F........
======================================================================
FAIL: test_crossings_outside_the_setup_range_are_not_scored (trader.tests.test_kucoin_radar.RadarTests.test_crossings_outside_the_setup_range_are_not_scored)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/Users/davidai/ZCodeProject/GrokBot-grid-kucoin/trader/tests/test_kucoin_radar.py", line 100, in test_crossings_outside_the_setup_range_are_not_scored
    self.assertEqual(crossing_score(bars, setup(), NOW)['score'], 0)
    ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
AssertionError: 59.75 != 0

----------------------------------------------------------------------
Ran 9 tests in 0.082s

FAILED (failures=1)
```

## pipeline-timeline-red

```text
...E..........
======================================================================
ERROR: test_equity_timeline_preserves_values_order_and_model_time (trader.tests.test_kucoin_tracker.TrackerTests.test_equity_timeline_preserves_values_order_and_model_time)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/Users/davidai/ZCodeProject/GrokBot-grid-kucoin/trader/tests/test_kucoin_tracker.py", line 147, in test_equity_timeline_preserves_values_order_and_model_time
    timeline = result['equity_timeline']
               ~~~~~~^^^^^^^^^^^^^^^^^^^
KeyError: 'equity_timeline'

----------------------------------------------------------------------
Ran 14 tests in 0.011s

FAILED (errors=1)
```

## replay-red

```text
    module = __import__(module_name)
  File "/Users/davidai/ZCodeProject/GrokBot-grid-kucoin/trader/tests/test_kucoin_replay.py", line 3, in <module>
    from trader.research.kucoin_replay import run_window
ModuleNotFoundError: No module named 'trader.research.kucoin_replay'


======================================================================
ERROR: test_kucoin_portfolio (unittest.loader._FailedTest.test_kucoin_portfolio)
----------------------------------------------------------------------
ImportError: Failed to import test module: test_kucoin_portfolio
Traceback (most recent call last):
  File "/opt/homebrew/Cellar/python@3.14/3.14.7/Frameworks/Python.framework/Versions/3.14/lib/python3.14/unittest/loader.py", line 141, in loadTestsFromName
    module = __import__(module_name)
  File "/Users/davidai/ZCodeProject/GrokBot-grid-kucoin/trader/tests/test_kucoin_portfolio.py", line 6, in <module>
    from trader.research.kucoin_portfolio import sweep, decision
ModuleNotFoundError: No module named 'trader.research.kucoin_portfolio'


----------------------------------------------------------------------
Ran 2 tests in 0.000s

FAILED (errors=2)
```

## monthly-target-red

```text
    ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
AssertionError: 'eligible_for_separate_hourly_recommender_review' != 'shelve'
- eligible_for_separate_hourly_recommender_review
+ shelve


======================================================================
FAIL: test_monthly_grid_target_gate_rejects_subtarget_seed_only_and_unknown_counts (trader.tests.test_kucoin_portfolio.ValidationTests.test_monthly_grid_target_gate_rejects_subtarget_seed_only_and_unknown_counts) (changes={'completed_at_target': None})
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/Users/davidai/ZCodeProject/GrokBot-grid-kucoin/trader/tests/test_kucoin_portfolio.py", line 113, in test_monthly_grid_target_gate_rejects_subtarget_seed_only_and_unknown_counts
    self.assertEqual(outcome['decision'], 'shelve')
    ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
AssertionError: 'eligible_for_separate_hourly_recommender_review' != 'shelve'
- eligible_for_separate_hourly_recommender_review
+ shelve


----------------------------------------------------------------------
Ran 9 tests in 0.005s

FAILED (failures=4)
```

## switch-spread-red

```text
======================================================================
FAIL: test_switch_forecast_and_actual_ledger_both_include_bid_ask_spread (trader.tests.test_kucoin_replay.ReplayTests.test_switch_forecast_and_actual_ledger_both_include_bid_ask_spread) (direction='long')
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/Users/davidai/ZCodeProject/GrokBot-grid-kucoin/trader/tests/test_kucoin_replay.py", line 197, in test_switch_forecast_and_actual_ledger_both_include_bid_ask_spread
    self.assertAlmostEqual(switch['switch_cost']['net_realized_on_close'], gross-fee)
    ~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
AssertionError: -0.3 != -0.5498499999999857 within 7 places (0.24984999999998575 difference)

======================================================================
FAIL: test_switch_forecast_and_actual_ledger_both_include_bid_ask_spread (trader.tests.test_kucoin_replay.ReplayTests.test_switch_forecast_and_actual_ledger_both_include_bid_ask_spread) (direction='short')
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/Users/davidai/ZCodeProject/GrokBot-grid-kucoin/trader/tests/test_kucoin_replay.py", line 197, in test_switch_forecast_and_actual_ledger_both_include_bid_ask_spread
    self.assertAlmostEqual(switch['switch_cost']['net_realized_on_close'], gross-fee)
    ~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
AssertionError: -0.3 != -0.5501499999999857 within 7 places (0.2501499999999857 difference)

----------------------------------------------------------------------
Ran 12 tests in 0.438s

FAILED (failures=2, errors=1)
```

## intrabar-cost-red

```text
======================================================================
FAIL: test_switch_forecast_and_actual_ledger_both_include_bid_ask_spread (trader.tests.test_kucoin_replay.ReplayTests.test_switch_forecast_and_actual_ledger_both_include_bid_ask_spread) (direction='long')
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/Users/davidai/ZCodeProject/GrokBot-grid-kucoin/trader/tests/test_kucoin_replay.py", line 197, in test_switch_forecast_and_actual_ledger_both_include_bid_ask_spread
    self.assertAlmostEqual(switch['switch_cost']['net_realized_on_close'], gross-fee)
    ~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
AssertionError: -0.3 != -0.5498499999999857 within 7 places (0.24984999999998575 difference)

======================================================================
FAIL: test_switch_forecast_and_actual_ledger_both_include_bid_ask_spread (trader.tests.test_kucoin_replay.ReplayTests.test_switch_forecast_and_actual_ledger_both_include_bid_ask_spread) (direction='short')
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/Users/davidai/ZCodeProject/GrokBot-grid-kucoin/trader/tests/test_kucoin_replay.py", line 197, in test_switch_forecast_and_actual_ledger_both_include_bid_ask_spread
    self.assertAlmostEqual(switch['switch_cost']['net_realized_on_close'], gross-fee)
    ~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
AssertionError: -0.3 != -0.5501499999999857 within 7 places (0.2501499999999857 difference)

----------------------------------------------------------------------
Ran 13 tests in 0.432s

FAILED (failures=3, errors=1)
```

## snapshot-red

```text
======================================================================
ERROR: test_absent_book_or_perpetual_facts_are_not_invented (trader.tests.test_kucoin_snapshot.SnapshotTests.test_absent_book_or_perpetual_facts_are_not_invented)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/Users/davidai/ZCodeProject/GrokBot-grid-kucoin/trader/tests/test_kucoin_snapshot.py", line 136, in test_absent_book_or_perpetual_facts_are_not_invented
    market = data.records(200)[0]['market']
             ^^^^^^^^^^^^
AttributeError: 'Snapshot' object has no attribute 'records'

======================================================================
ERROR: test_records_keep_oldest_fact_time_and_contract_units (trader.tests.test_kucoin_snapshot.SnapshotTests.test_records_keep_oldest_fact_time_and_contract_units)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/Users/davidai/ZCodeProject/GrokBot-grid-kucoin/trader/tests/test_kucoin_snapshot.py", line 119, in test_records_keep_oldest_fact_time_and_contract_units
    records = data.records(60000)
              ^^^^^^^^^^^^
AttributeError: 'Snapshot' object has no attribute 'records'

----------------------------------------------------------------------
Ran 10 tests in 0.041s

FAILED (errors=2)
```

## snapshot-schema-red

```text
  File "/Users/davidai/ZCodeProject/GrokBot-grid-kucoin/trader/research/kucoin_snapshot.py", line 95, in _validate_schema
    raise SnapshotError('offline snapshot schema is incomplete')
trader.research.kucoin_snapshot.SnapshotError: offline snapshot schema is incomplete

======================================================================
ERROR: test_records_keep_oldest_fact_time_and_contract_units (trader.tests.test_kucoin_snapshot.SnapshotTests.test_records_keep_oldest_fact_time_and_contract_units)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/Users/davidai/ZCodeProject/GrokBot-grid-kucoin/trader/tests/test_kucoin_snapshot.py", line 118, in test_records_keep_oldest_fact_time_and_contract_units
    with Snapshot(self.path) as data:
         ~~~~~~~~^^^^^^^^^^^
  File "/Users/davidai/ZCodeProject/GrokBot-grid-kucoin/trader/research/kucoin_snapshot.py", line 75, in __init__
    self._validate_schema()
    ~~~~~~~~~~~~~~~~~~~~~^^
  File "/Users/davidai/ZCodeProject/GrokBot-grid-kucoin/trader/research/kucoin_snapshot.py", line 95, in _validate_schema
    raise SnapshotError('offline snapshot schema is incomplete')
trader.research.kucoin_snapshot.SnapshotError: offline snapshot schema is incomplete

----------------------------------------------------------------------
Ran 10 tests in 0.050s

FAILED (errors=5)
```

## radar-coverage-red

```text
======================================================================
ERROR: test_rejection_distinguishes_missing_data_from_known_filter_failure (trader.tests.test_kucoin_radar.RadarTests.test_rejection_distinguishes_missing_data_from_known_filter_failure) (changes={})
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/Users/davidai/ZCodeProject/GrokBot-grid-kucoin/trader/tests/test_kucoin_radar.py", line 84, in test_rejection_distinguishes_missing_data_from_known_filter_failure
    self.assertEqual(result['rejected'][0]['coverage_issue'], expected)
                     ~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^
KeyError: 'coverage_issue'

======================================================================
FAIL: test_score_uses_accepted_tick_interval_not_a_finer_recomputed_grid (trader.tests.test_kucoin_radar.RadarTests.test_score_uses_accepted_tick_interval_not_a_finer_recomputed_grid)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/Users/davidai/ZCodeProject/GrokBot-grid-kucoin/trader/tests/test_kucoin_radar.py", line 88, in test_score_uses_accepted_tick_interval_not_a_finer_recomputed_grid
    self.assertEqual(crossing_score(candles(), form, NOW)['step'], 2)
    ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
AssertionError: 1.0 != 2

----------------------------------------------------------------------
Ran 13 tests in 0.097s

FAILED (failures=1, errors=8)
```

## operator-red

```text
    module = __import__(module_name)
  File "/Users/davidai/ZCodeProject/GrokBot-grid-kucoin/trader/tests/test_kucoin_operator.py", line 3, in <module>
    from trader.research.kucoin_operator import operator_report, validate_running
ModuleNotFoundError: No module named 'trader.research.kucoin_operator'


======================================================================
ERROR: test_kucoin_cli (unittest.loader._FailedTest.test_kucoin_cli)
----------------------------------------------------------------------
ImportError: Failed to import test module: test_kucoin_cli
Traceback (most recent call last):
  File "/opt/homebrew/Cellar/python@3.14/3.14.7/Frameworks/Python.framework/Versions/3.14/lib/python3.14/unittest/loader.py", line 141, in loadTestsFromName
    module = __import__(module_name)
  File "/Users/davidai/ZCodeProject/GrokBot-grid-kucoin/trader/tests/test_kucoin_cli.py", line 8, in <module>
    from trader.research.kucoin_cli import main, persist_report, load_running
ModuleNotFoundError: No module named 'trader.research.kucoin_cli'


----------------------------------------------------------------------
Ran 2 tests in 0.000s

FAILED (errors=2)
```

## operator-edge-red

```text
======================================================================
FAIL: test_running_form_rejects_leverage_outside_three_to_six (trader.tests.test_kucoin_operator.OperatorTests.test_running_form_rejects_leverage_outside_three_to_six)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/Users/davidai/ZCodeProject/GrokBot-grid-kucoin/trader/tests/test_kucoin_operator.py", line 98, in test_running_form_rejects_leverage_outside_three_to_six
    with self.assertRaises(ValueError):
         ~~~~~~~~~~~~~~~~~^^^^^^^^^^^^
AssertionError: ValueError not raised

======================================================================
FAIL: test_submillisecond_asof_is_rejected_without_rounding (trader.tests.test_kucoin_cli.CliTests.test_submillisecond_asof_is_rejected_without_rounding)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/Users/davidai/ZCodeProject/GrokBot-grid-kucoin/trader/tests/test_kucoin_cli.py", line 91, in test_submillisecond_asof_is_rejected_without_rounding
    with self.assertRaises(ValueError):
         ~~~~~~~~~~~~~~~~~^^^^^^^^^^^^
AssertionError: ValueError not raised

----------------------------------------------------------------------
Ran 16 tests in 0.132s

FAILED (failures=3)
```

## operator-quoted-cost-red

```text
........F
======================================================================
FAIL: test_switch_cost_uses_executable_quote_while_tracker_keeps_mark (trader.tests.test_kucoin_operator.OperatorTests.test_switch_cost_uses_executable_quote_while_tracker_keeps_mark)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/Users/davidai/ZCodeProject/GrokBot-grid-kucoin/trader/tests/test_kucoin_operator.py", line 120, in test_switch_cost_uses_executable_quote_while_tracker_keeps_mark
    self.assertAlmostEqual(cost['floating_pnl'], -.25)
    ~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^
AssertionError: 0 != -0.25 within 7 places (0.25 difference)

----------------------------------------------------------------------
Ran 9 tests in 0.103s

FAILED (failures=1)
```

## core-range-stop-red

```text
Traceback (most recent call last):
  File "/Users/davidai/ZCodeProject/GrokBot-grid-kucoin/trader/tests/test_kucoin_grid.py", line 345, in test_five_percent_outside_range_stops_all_modes_both_edges
    self.assertEqual(at_edge.status, 'stopped')
    ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^
AssertionError: 'out_of_range' != 'stopped'
- out_of_range
+ stopped


======================================================================
FAIL: test_range_stop_gap_uses_observed_execution_price (trader.tests.test_kucoin_grid.GridTests.test_range_stop_gap_uses_observed_execution_price)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/Users/davidai/ZCodeProject/GrokBot-grid-kucoin/trader/tests/test_kucoin_grid.py", line 362, in test_range_stop_gap_uses_observed_execution_price
    self.assertEqual(outside.stop_reason, 'stop_loss')
    ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
AssertionError: None != 'stop_loss'

----------------------------------------------------------------------
Ran 43 tests in 0.025s

FAILED (failures=7, errors=2)
```

## setup-stop-structure-red

```text
======================================================================
ERROR: test_five_percent_both_edge_stops_are_price_based_for_all_modes (trader.tests.test_grid_setup.SetupTests.test_five_percent_both_edge_stops_are_price_based_for_all_modes)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/Users/davidai/ZCodeProject/GrokBot-grid-kucoin/trader/tests/test_grid_setup.py", line 190, in test_five_percent_both_edge_stops_are_price_based_for_all_modes
    self.assertAlmostEqual(result['range_exit_stop_low'],.95*result['low'])
                           ~~~~~~^^^^^^^^^^^^^^^^^^^^^^^
KeyError: 'range_exit_stop_low'

======================================================================
FAIL: test_range_uses_confirmed_support_resistance_and_explains_fallback (trader.tests.test_grid_setup.SetupTests.test_range_uses_confirmed_support_resistance_and_explains_fallback)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/Users/davidai/ZCodeProject/GrokBot-grid-kucoin/trader/tests/test_grid_setup.py", line 205, in test_range_uses_confirmed_support_resistance_and_explains_fallback
    self.assertGreaterEqual(result['low'],92.)
    ~~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^
AssertionError: 85.03 not greater than or equal to 92.0

----------------------------------------------------------------------
Ran 4 tests in 0.132s

FAILED (failures=1, errors=3)
```

## tracker-range-stop-red

```text
======================================================================
FAIL: test_terminal_stop_retains_preclose_liquidation_distance (trader.tests.test_kucoin_tracker.TrackerTests.test_terminal_stop_retains_preclose_liquidation_distance)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/Users/davidai/ZCodeProject/GrokBot-grid-kucoin/trader/tests/test_kucoin_tracker.py", line 135, in test_terminal_stop_retains_preclose_liquidation_distance
    self.assertEqual(summary['distance_stop_loss_pct'], 0)
    ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
AssertionError: 0.5882352941176471 != 0

======================================================================
FAIL: test_upper_hard_stop_is_reported_for_long_without_a_custom_upper_stop (trader.tests.test_kucoin_tracker.TrackerTests.test_upper_hard_stop_is_reported_for_long_without_a_custom_upper_stop)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/Users/davidai/ZCodeProject/GrokBot-grid-kucoin/trader/tests/test_kucoin_tracker.py", line 197, in test_upper_hard_stop_is_reported_for_long_without_a_custom_upper_stop
    self.assertEqual(summary['distance_stop_loss_pct'], 0)
    ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
AssertionError: None != 0

----------------------------------------------------------------------
Ran 18 tests in 0.036s

FAILED (failures=2, errors=4)
```

## baseline-stop-red

```text
======================================================================
FAIL: test_unchanged_historical_forms_do_not_invent_new_range_stops (trader.tests.test_grid_calibration.CalibrationTests.test_unchanged_historical_forms_do_not_invent_new_range_stops)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/Users/davidai/ZCodeProject/GrokBot-grid-kucoin/trader/tests/test_grid_calibration.py", line 42, in test_unchanged_historical_forms_do_not_invent_new_range_stops
    self.assertIsNone(config.range_exit_stop_pct)
    ~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^
AssertionError: 0.05 is not None

----------------------------------------------------------------------
Ran 10 tests in 0.023s

FAILED (failures=1)
```

## stop-adapter-red

```text
======================================================================
FAIL: test_tick_aligned_form_stops_survive_both_config_adapters (trader.tests.test_grid_stop_integration.StopFormIntegrationTests.test_tick_aligned_form_stops_survive_both_config_adapters) (direction='long', adapter='trader.research.kucoin_replay')
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/Users/davidai/ZCodeProject/GrokBot-grid-kucoin/trader/tests/test_grid_stop_integration.py", line 19, in test_tick_aligned_form_stops_survive_both_config_adapters
    self.assertEqual(config.tick_size, .01)
    ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^
AssertionError: None != 0.01

======================================================================
FAIL: test_tick_aligned_form_stops_survive_both_config_adapters (trader.tests.test_grid_stop_integration.StopFormIntegrationTests.test_tick_aligned_form_stops_survive_both_config_adapters) (direction='long', adapter='trader.research.kucoin_operator')
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/Users/davidai/ZCodeProject/GrokBot-grid-kucoin/trader/tests/test_grid_stop_integration.py", line 19, in test_tick_aligned_form_stops_survive_both_config_adapters
    self.assertEqual(config.tick_size, .01)
    ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^
AssertionError: None != 0.01

----------------------------------------------------------------------
Ran 1 test in 0.001s

FAILED (failures=2, errors=1)
```

## Streaming correction

```text
======================================================================
ERROR: test_universe_iterator_materializes_only_the_next_coins_history (trader.tests.test_kucoin_snapshot.SnapshotTests.test_universe_iterator_materializes_only_the_next_coins_history)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/Users/davidai/ZCodeProject/GrokBot-grid-kucoin/trader/tests/test_kucoin_snapshot.py", line 154, in test_universe_iterator_materializes_only_the_next_coins_history
    stream = data.iter_records(200)
             ^^^^^^^^^^^^^^^^^
AttributeError: 'Snapshot' object has no attribute 'iter_records'

----------------------------------------------------------------------
Ran 11 tests in 0.051s

FAILED (errors=1)

```
