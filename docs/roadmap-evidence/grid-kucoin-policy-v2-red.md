# Policy-v2 red-first evidence

Intentional failures captured before each correction, not final test results.

## grid-policy-v2-core-red.log

```text
    return self.__class__(**changes)
           ~~~~~~~~~~~~~~^^^^^^^^^^^
TypeError: GridConfig.__init__() got an unexpected keyword argument 'adaptive_range_stops'

======================================================================
ERROR: test_adaptive_unsafe_one_percent_stops_immediately_with_risk_reason (trader.tests.test_kucoin_grid.GridTests.test_adaptive_unsafe_one_percent_stops_immediately_with_risk_reason)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/Users/davidai/ZCodeProject/GrokBot-grid-policy-v2/trader/tests/test_kucoin_grid.py", line 403, in test_adaptive_unsafe_one_percent_stops_immediately_with_risk_reason
    state = create_bot(self.config(direction='long', investment=200, leverage=10,
                       ~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
                                   adaptive_range_stops=True), 100, 0)
                                   ^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/Users/davidai/ZCodeProject/GrokBot-grid-policy-v2/trader/tests/test_kucoin_grid.py", line 8, in config
    return GridConfig(pair='TESTUSDTM', low=90, high=110, grids=20,
                      quantity=1, **kw)
TypeError: GridConfig.__init__() got an unexpected keyword argument 'adaptive_range_stops'

----------------------------------------------------------------------
Ran 50 tests in 0.030s

FAILED (errors=7)
```

## grid-policy-v2-core-risk-edge-red.log

```text
.........F...........................................
======================================================================
FAIL: test_already_insolvent_adaptive_state_is_liquidation_not_protective_success (trader.tests.test_kucoin_grid.GridTests.test_already_insolvent_adaptive_state_is_liquidation_not_protective_success)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/Users/davidai/ZCodeProject/GrokBot-grid-policy-v2/trader/tests/test_kucoin_grid.py", line 475, in test_already_insolvent_adaptive_state_is_liquidation_not_protective_success
    self.assertTrue(stopped.liquidated)
    ~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^
AssertionError: False is not true

----------------------------------------------------------------------
Ran 53 tests in 0.050s

FAILED (failures=1)
```

## grid-policy-v2-setup-red.log

```text
-  0,
-  0,
-  0,
-  0,
-  0,
-  0,
-  0,
-  0,
-  0,
-  0,
-  0,
-  0,
-  0,
-  0,
-  0,
-  0,
-  1]

----------------------------------------------------------------------
Ran 19 tests in 1.918s

FAILED (failures=10)
```

## grid-policy-v2-setup-adaptive-red.log

```text
E
======================================================================
ERROR: test_new_setup_enables_adaptive_stops_without_changing_historical_defaults (trader.tests.test_grid_setup.SetupTests.test_new_setup_enables_adaptive_stops_without_changing_historical_defaults)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/Users/davidai/ZCodeProject/GrokBot-grid-policy-v2/trader/tests/test_grid_setup.py", line 235, in test_new_setup_enables_adaptive_stops_without_changing_historical_defaults
    self.assertTrue(result['adaptive_range_stops'])
                    ~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^
KeyError: 'adaptive_range_stops'

----------------------------------------------------------------------
Ran 1 test in 0.054s

FAILED (errors=1)
```

## grid-policy-v2-stop-preview-red.log

```text
======================================================================
FAIL: test_preview_reports_tick_valid_stops_on_both_sides (trader.tests.test_grid_setup.SetupTests.test_preview_reports_tick_valid_stops_on_both_sides)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/Users/davidai/ZCodeProject/GrokBot-grid-policy-v2/trader/tests/test_grid_setup.py", line 52, in test_preview_reports_tick_valid_stops_on_both_sides
    self.assertEqual(result['preview']['effective_stop_loss_'+side],
    ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
                     result['hard_stop_'+side])
                     ^^^^^^^^^^^^^^^^^^^^^^^^^^
AssertionError: 146.958 != 146.95

----------------------------------------------------------------------
Ran 21 tests in 1.625s

FAILED (failures=1)
```

## gridpolicy-v2-policy-red.log

```text
  File "/Users/davidai/ZCodeProject/GrokBot-grid-policy-v2/trader/tests/test_kucoin_replacement.py", line 134, in test_one_percent_liquidation_emergency_is_separate_from_ordinary_switch
    result = self.decide([policy_current('RISK', 10, distance_liquidation_pct=.8),
                         policy_current('WORST', 3)], [policy_candidate(score=50)])
  File "/Users/davidai/ZCodeProject/GrokBot-grid-policy-v2/trader/tests/test_kucoin_replacement.py", line 105, in decide
    from trader.research.kucoin_replacement import decide_portfolio_replacement
ImportError: cannot import name 'decide_portfolio_replacement' from 'trader.research.kucoin_replacement' (/Users/davidai/ZCodeProject/GrokBot-grid-policy-v2/trader/research/kucoin_replacement.py)

======================================================================
ERROR: test_switch_loss_and_opening_fees_can_defeat_higher_grid_rate (trader.tests.test_kucoin_replacement.PortfolioReplacementTests.test_switch_loss_and_opening_fees_can_defeat_higher_grid_rate)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/Users/davidai/ZCodeProject/GrokBot-grid-policy-v2/trader/tests/test_kucoin_replacement.py", line 119, in test_switch_loss_and_opening_fees_can_defeat_higher_grid_rate
    result = self.decide([policy_current('GOOD', 10), policy_current('WORST', 3, floating_pnl=-100)],
                         [policy_candidate(score=6)])
  File "/Users/davidai/ZCodeProject/GrokBot-grid-policy-v2/trader/tests/test_kucoin_replacement.py", line 105, in decide
    from trader.research.kucoin_replacement import decide_portfolio_replacement
ImportError: cannot import name 'decide_portfolio_replacement' from 'trader.research.kucoin_replacement' (/Users/davidai/ZCodeProject/GrokBot-grid-policy-v2/trader/research/kucoin_replacement.py)

----------------------------------------------------------------------
Ran 14 tests in 0.001s

FAILED (errors=5)
```

## gridpolicy-v2-radar-red.log

```text
  File "/opt/homebrew/Cellar/python@3.14/3.14.7/Frameworks/Python.framework/Versions/3.14/lib/python3.14/unittest/mock.py", line 1439, in patched
    return func(*newargs, **newkeywargs)
  File "/Users/davidai/ZCodeProject/GrokBot-grid-policy-v2/trader/tests/test_kucoin_radar.py", line 137, in test_default_ten_and_configurable_five_preserve_proxy_context
    self.assertEqual(len(result['radar']), 10)
    ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^
AssertionError: 5 != 10

======================================================================
FAIL: test_filter_table_and_always_exclude_running (trader.tests.test_kucoin_radar.RadarTests.test_filter_table_and_always_exclude_running)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/opt/homebrew/Cellar/python@3.14/3.14.7/Frameworks/Python.framework/Versions/3.14/lib/python3.14/unittest/mock.py", line 1439, in patched
    return func(*newargs, **newkeywargs)
  File "/Users/davidai/ZCodeProject/GrokBot-grid-policy-v2/trader/tests/test_kucoin_radar.py", line 70, in test_filter_table_and_always_exclude_running
    self.assertEqual(len(result['radar']), 7)
    ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^
AssertionError: 5 != 7

----------------------------------------------------------------------
Ran 14 tests in 0.162s

FAILED (failures=2)
```

## gridpolicy-v2-replay-red.log

```text
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/Users/davidai/ZCodeProject/GrokBot-grid-policy-v2/trader/tests/test_kucoin_replay.py", line 191, in test_switch_forecast_and_actual_ledger_both_include_bid_ask_spread
    self.assertTrue(result['coverage']['complete'])
    ~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
AssertionError: False is not true

======================================================================
FAIL: test_two_slots_total_margin_and_fill_identity (trader.tests.test_kucoin_replay.ReplayTests.test_two_slots_total_margin_and_fill_identity)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/opt/homebrew/Cellar/python@3.14/3.14.7/Frameworks/Python.framework/Versions/3.14/lib/python3.14/unittest/mock.py", line 1439, in patched
    return func(*newargs, **newkeywargs)
  File "/Users/davidai/ZCodeProject/GrokBot-grid-policy-v2/trader/tests/test_kucoin_replay.py", line 52, in test_two_slots_total_margin_and_fill_identity
    self.assertEqual(result['initial_capital'], 2400)
    ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
AssertionError: 2000.0 != 2400

----------------------------------------------------------------------
Ran 16 tests in 0.008s

FAILED (failures=10, errors=2)
```

## gridpolicy-v2-capital-red.log

```text
    ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^
AssertionError: 'replace' != 'keep'
- replace
+ keep


======================================================================
FAIL: test_missing_available_cash_never_authorizes_a_replacement (trader.tests.test_kucoin_replacement.PortfolioReplacementTests.test_missing_available_cash_never_authorizes_a_replacement) (options={'available_cash': None})
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/Users/davidai/ZCodeProject/GrokBot-grid-policy-v2/trader/tests/test_kucoin_replacement.py", line 172, in test_missing_available_cash_never_authorizes_a_replacement
    self.assertEqual(result['action'], 'keep')
    ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^
AssertionError: 'replace' != 'keep'
- replace
+ keep


----------------------------------------------------------------------
Ran 20 tests in 0.002s

FAILED (failures=2, errors=3)
```

## grid-policy-v2-operator-red.log

```text
    ~~~~~~~~~~~~~^^^^^^^^^^^^^^
  File "/Users/davidai/ZCodeProject/GrokBot-grid-policy-v2/trader/research/kucoin_operator.py", line 59, in _validate_bot
    raise ValueError('used margin plus reserve must equal 1000 USDT')
ValueError: used margin plus reserve must equal 1000 USDT

======================================================================
FAIL: test_missing_candles_and_funding_fail_closed (trader.tests.test_kucoin_operator.OperatorTests.test_missing_candles_and_funding_fail_closed)
----------------------------------------------------------------------
ValueError: used margin plus reserve must equal 1000 USDT

During handling of the above exception, another exception occurred:

Traceback (most recent call last):
  File "/Users/davidai/ZCodeProject/GrokBot-grid-policy-v2/trader/tests/test_kucoin_operator.py", line 77, in test_missing_candles_and_funding_fail_closed
    with self.assertRaisesRegex(ValueError, 'candles'):
         ~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^
AssertionError: "candles" does not match "used margin plus reserve must equal 1000 USDT"

----------------------------------------------------------------------
Ran 13 tests in 0.003s

FAILED (failures=1, errors=9)
```

## grid-policy-v2-tracker-red.log

```text
======================================================================
FAIL: test_latched_low_stop_is_reported_instead_of_static_five_percent_preview (trader.tests.test_kucoin_tracker.TrackerTests.test_latched_low_stop_is_reported_instead_of_static_five_percent_preview)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/Users/davidai/ZCodeProject/GrokBot-grid-policy-v2/trader/tests/test_kucoin_tracker.py", line 206, in test_latched_low_stop_is_reported_instead_of_static_five_percent_preview
    self.assertEqual(summary['range_exit_stop_low'], 89.1)
    ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
AssertionError: 85.5 != 89.1

======================================================================
FAIL: test_latched_upper_stop_retains_custom_nearer_stop_and_reported_reason (trader.tests.test_kucoin_tracker.TrackerTests.test_latched_upper_stop_retains_custom_nearer_stop_and_reported_reason)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/Users/davidai/ZCodeProject/GrokBot-grid-policy-v2/trader/tests/test_kucoin_tracker.py", line 221, in test_latched_upper_stop_retains_custom_nearer_stop_and_reported_reason
    self.assertEqual(summary['range_exit_stop_high'], 111.1)
    ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
AssertionError: 115.5 != 111.1

----------------------------------------------------------------------
Ran 20 tests in 0.024s

FAILED (failures=2)
```

## grid-policy-v2-emergency-red.log

```text
F.....................
======================================================================
FAIL: test_adaptive_early_warning_does_not_offer_more_than_fixed_reserve (trader.tests.test_kucoin_tracker.TrackerTests.test_adaptive_early_warning_does_not_offer_more_than_fixed_reserve)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/Users/davidai/ZCodeProject/GrokBot-grid-policy-v2/trader/tests/test_kucoin_tracker.py", line 246, in test_adaptive_early_warning_does_not_offer_more_than_fixed_reserve
    self.assertEqual(warning['emergency_action'],
    ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
        'verify tightened 1% range protection or stop; no additional margin beyond fixed reserve')
        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
AssertionError: 'stop or add reserve before liquidation' != 'verify tightened 1% range protection or s[42 chars]erve'
- stop or add reserve before liquidation
+ verify tightened 1% range protection or stop; no additional margin beyond fixed reserve


----------------------------------------------------------------------
Ran 22 tests in 0.029s

FAILED (failures=1)
```

## grid-policy-v2-operator-capital-red.log

```text
+ []
- [{'bot_id': 'test-bot',
-   'direction': 'long',
-   'eligible': True,
-   'entry': 100,
-   'expected_start_gph': 10,
-   'grids': 20,
-   'high': 110,
-   'leverage': 5,
-   'low': 90,
-   'pair': 'NEW',
-   'preview': {'profit_per_grid_min': 1},
-   'quantity': 1,
-   'reserved_margin': 200,
-   'start_ms': 0,
-   'stop_loss': 85,
-   'used_margin': 1000}]

----------------------------------------------------------------------
Ran 18 tests in 1.683s

FAILED (failures=1, errors=7)
```

## grid-policy-v2-operator-radar-size-red.log

```text
======================================================================
ERROR: test_operator_propagates_requested_radar_size_and_defaults_to_ten (trader.tests.test_kucoin_operator.OperatorTests.test_operator_propagates_requested_radar_size_and_defaults_to_ten) (parameters={})
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/Users/davidai/ZCodeProject/GrokBot-grid-policy-v2/trader/tests/test_kucoin_operator.py", line 249, in test_operator_propagates_requested_radar_size_and_defaults_to_ten
    self.assertEqual(mocked.call_args.args[3]['radar_size'], expected)
                     ~~~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^
KeyError: 'radar_size'

======================================================================
ERROR: test_operator_propagates_requested_radar_size_and_defaults_to_ten (trader.tests.test_kucoin_operator.OperatorTests.test_operator_propagates_requested_radar_size_and_defaults_to_ten) (parameters={'radar_size': 5})
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/Users/davidai/ZCodeProject/GrokBot-grid-policy-v2/trader/tests/test_kucoin_operator.py", line 249, in test_operator_propagates_requested_radar_size_and_defaults_to_ten
    self.assertEqual(mocked.call_args.args[3]['radar_size'], expected)
                     ~~~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^
KeyError: 'radar_size'

----------------------------------------------------------------------
Ran 1 test in 0.001s

FAILED (errors=2)
```

## grid-policy-v2-timer-red.log

```text
======================================================================
ERROR: test_paper_evaluation (unittest.loader._FailedTest.test_paper_evaluation)
----------------------------------------------------------------------
ImportError: Failed to import test module: test_paper_evaluation
Traceback (most recent call last):
  File "/opt/homebrew/Cellar/python@3.14/3.14.7/Frameworks/Python.framework/Versions/3.14/lib/python3.14/unittest/loader.py", line 141, in loadTestsFromName
    module = __import__(module_name)
  File "/Users/davidai/ZCodeProject/GrokBot-grid-policy-v2/trader/tests/test_paper_evaluation.py", line 3, in <module>
    from trader.research.paper_evaluation import arm_evaluation, start_evaluation, evaluation_clock
ModuleNotFoundError: No module named 'trader.research.paper_evaluation'


----------------------------------------------------------------------
Ran 1 test in 0.000s

FAILED (errors=1)
```

## grid-policy-v2-timer-seal-red.log

```text
TypeError: arm_evaluation() got an unexpected keyword argument 'source_revision'

======================================================================
ERROR: test_repeated_start_cannot_reset_a_running_timer_or_reuse_old_run (trader.tests.test_paper_evaluation.PaperEvaluationTests.test_repeated_start_cannot_reset_a_running_timer_or_reuse_old_run)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/Users/davidai/ZCodeProject/GrokBot-grid-policy-v2/trader/tests/test_paper_evaluation.py", line 60, in test_repeated_start_cannot_reset_a_running_timer_or_reuse_old_run
    plan = arm_evaluation(POLICY, previous_run_id='old-run', source_revision=REVISION)
TypeError: arm_evaluation() got an unexpected keyword argument 'source_revision'

======================================================================
ERROR: test_source_revision_is_sealed_into_the_paper_clock (trader.tests.test_paper_evaluation.PaperEvaluationTests.test_source_revision_is_sealed_into_the_paper_clock)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/Users/davidai/ZCodeProject/GrokBot-grid-policy-v2/trader/tests/test_paper_evaluation.py", line 49, in test_source_revision_is_sealed_into_the_paper_clock
    first = arm_evaluation(POLICY, source_revision=REVISION)
TypeError: arm_evaluation() got an unexpected keyword argument 'source_revision'

----------------------------------------------------------------------
Ran 5 tests in 0.001s

FAILED (errors=5)
```

## grid-policy-v2-timer-record-red.log

```text
======================================================================
FAIL: test_inconsistent_persisted_clock_cannot_move_the_audit_deadline (trader.tests.test_paper_evaluation.PaperEvaluationTests.test_inconsistent_persisted_clock_cannot_move_the_audit_deadline)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/Users/davidai/ZCodeProject/GrokBot-grid-policy-v2/trader/tests/test_paper_evaluation.py", line 66, in test_inconsistent_persisted_clock_cannot_move_the_audit_deadline
    with self.assertRaises(ValueError):
         ~~~~~~~~~~~~~~~~~^^^^^^^^^^^^
AssertionError: ValueError not raised

----------------------------------------------------------------------
Ran 6 tests in 0.001s

FAILED (failures=1)
```
