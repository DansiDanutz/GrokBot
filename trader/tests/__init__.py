"""Offline tests for isolated trader components.

Policy decide/advance cycles load the learned-rules store; point the
default at a path that never exists so no test depends on the live store
in ~/Sandbox (tests that exercise gates inject their own store).
"""
import os as _os

from trader.review import rules as _rules

_rules.DEFAULT_STORE_PATH = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)),
                                          ".no-such-learned-rules.json")
