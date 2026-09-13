"""Optional, additive radar annotation from derived liquidation clusters.

The cluster file is advisory: it never changes a score, a range, a section or
an admission decision. A missing, unreadable, wrong-schema or stale file
annotates zeros rather than failing the radar run.
"""

import json
import math
import sys
from pathlib import Path

SCHEMA_VERSION = 1
MAX_AGE_MS = 3 * 3_600_000
# Rebuilt by the hourly radar itself rather than a second scheduled job, so the
# annotation works on a machine where only the radar is installed.
REFRESH_AFTER_MS = 90 * 60_000
REFRESH_MAX_SYMBOLS = 60
DEFAULT_PATH = str(Path.home() / 'Sandbox' / 'grokbot' / 'market-data'
                   / 'liquidation-clusters.json')
MAX_FILE_BYTES = 32_000_000
FIELDS = ('liq_below_pct', 'liq_below_usd', 'liq_above_pct', 'liq_above_usd')
ZEROS = dict.fromkeys(FIELDS, 0.0)


def _finite(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    number = float(value)
    return number if math.isfinite(number) else 0.0


def _read(path):
    target = Path(path).expanduser().absolute()
    if not target.is_file() or target.is_symlink() \
            or target.stat().st_size > MAX_FILE_BYTES:
        return None
    try:
        report = json.loads(target.read_text())
    except (OSError, ValueError):
        return None
    return report if isinstance(report, dict) else None


def load(path, now_ms):
    """Return (symbol entries, generated_at_ms); ({}, None) when unusable."""
    report = _read(path) if path else None
    if not report or report.get('schema_version') != SCHEMA_VERSION:
        return {}, None
    generated = report.get('generated_at_ms')
    if isinstance(generated, bool) or not isinstance(generated, int) \
            or not 0 <= now_ms - generated <= MAX_AGE_MS:
        return {}, None
    symbols = report.get('symbols')
    return (symbols if isinstance(symbols, dict) else {}), generated


def fields(entry):
    """The four additive row fields; unknown levels annotate 0.0, never null."""
    if not isinstance(entry, dict):
        return dict(ZEROS)
    below = entry.get('nearest_below')
    above = entry.get('nearest_above')
    annotated = dict(ZEROS)
    for side, prefix in ((below, 'below'), (above, 'above')):
        if isinstance(side, dict):
            annotated['liq_' + prefix + '_pct'] = _finite(side.get('distance_pct'))
            annotated['liq_' + prefix + '_usd'] = _finite(side.get('usd'))
    return annotated


def annotate(report, path, now_ms):
    """Return a new radar report whose rows and sections carry the levels."""
    symbols, generated = load(path, now_ms)
    rows = [{**row, **fields(symbols.get(row['symbol']))}
            for row in report['rows']]
    index = {row['symbol']: row for row in rows}
    sections = {name: [index[item['symbol']] for item in items]
                for name, items in report['sections'].items()}
    return {**report, 'rows': rows, 'sections': sections,
            'liq_clusters_generated_at_ms': generated}


def _fresh_enough(path, now_ms):
    report = _read(path)
    if not report or report.get('schema_version') != SCHEMA_VERSION:
        return False
    generated = report.get('generated_at_ms')
    return (not isinstance(generated, bool) and isinstance(generated, int)
            and 0 <= now_ms - generated <= REFRESH_AFTER_MS)


def refresh(path, database, symbols, now_ms, limit=REFRESH_MAX_SYMBOLS):
    """Rebuild the cluster file in place when it is missing or stale.

    Returns the path either way. Deriving clusters is advisory work on the
    radar's critical path, so every failure is reported and swallowed: a scan
    that cannot annotate is still a valid scan, and annotate() zeroes the
    fields. The alternative - a second scheduled job - silently annotates
    zeros forever on a machine where nobody installed it.
    """
    if not path or not symbols:
        return path
    try:
        if _fresh_enough(path, now_ms):
            return path
        from trader.data.liquidation_clusters import build, write_report
        wanted = list(dict.fromkeys(symbols))[:limit]
        write_report(path, build(database, wanted, now_ms))
    except Exception as error:                     # advisory: never fail a scan
        print('liquidation cluster refresh skipped: ' + type(error).__name__,
              file=sys.stderr, flush=True)
    return path
