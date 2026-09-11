"""Bounded exception locations; never store exception text, source lines or locals."""
from collections import deque
from pathlib import Path
import re
from paper_grid.telemetry_constants import MAX_TRACEBACK_FRAMES, MAX_TRACEBACK_TEXT

SOURCE_DIRECTORY = Path(__file__).resolve().parent
NAME = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')
ERROR_TYPES = (ValueError, TypeError, RuntimeError, TimeoutError, OSError,
               ArithmeticError, LookupError, AssertionError)
REASONS = {
    'cycle': 'cycle aborted; both account states preserved',
    'features': 'CoinGlass collection failed',
    'new_features': 'CoinGlass new-symbol collection failed',
}


def _frame(frame, line):
    code = frame.f_code
    path = Path(code.co_filename)
    trusted = (path.parent == SOURCE_DIRECTORY and path.suffix == '.py'
               and NAME.fullmatch(path.stem) and len(path.name) <= MAX_TRACEBACK_TEXT)
    function = code.co_name
    named = NAME.fullmatch(function) and len(function) <= MAX_TRACEBACK_TEXT
    return dict(file=path.name if trusted else '<external>',
                function=function if trusted and named else '<external>', line=line)


def error_record(error, tick_at, kind='cycle'):
    frames = deque(maxlen=MAX_TRACEBACK_FRAMES)
    count, trace = 0, error.__traceback__
    while trace is not None:
        frames.append(_frame(trace.tb_frame, trace.tb_lineno))
        count += 1
        trace = trace.tb_next
    category = next((cls.__name__ for cls in ERROR_TYPES if isinstance(error, cls)), 'Exception')
    return dict(time=tick_at, tick_at=tick_at, type=category, reason=REASONS[kind],
                traceback=list(frames), traceback_frame_count=count,
                traceback_truncated=count > MAX_TRACEBACK_FRAMES)
