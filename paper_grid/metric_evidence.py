"""Windowed retained observations and indexed lifetime lifecycle events."""
import json
import os
import stat
from datetime import datetime
from pathlib import Path

from paper_grid import engine, metric_index, retention, trade_metrics
from paper_grid.metric_constants import ARMS, EVIDENCE_MAX_BYTES, MAX_REPORTED_TRADES


def _paths(runtime, doc, end):
    runtime = Path(runtime)
    if any(path.is_symlink() for path in (runtime, *runtime.parents)) or not runtime.is_dir():
        raise ValueError('audit metric runtime is missing or unsafe')
    start = retention._timestamp(doc['start_at'])
    if retention._timestamp(end) < start:
        raise ValueError('invalid audit metric history range')
    last = retention._day(end)
    directory = retention._directory(runtime)
    paths = sorted(p for p in directory.iterdir() if retention.FILENAME.fullmatch(p.name)
                   and p.stem <= last) if directory.exists() else []
    declared = doc.get('archive_files', [])
    if not isinstance(declared, list):
        raise ValueError('invalid declared archive files')
    available = {p.name for p in paths}
    for name in declared:
        if not isinstance(name, str) or not retention.FILENAME.fullmatch(name):
            raise ValueError('invalid declared archive filename')
        datetime.strptime(name[:-5], '%Y-%m-%d')
        if name[:-5] <= last and name not in available:
            raise ValueError('required observation archive is missing or unsafe')
    if len(json.dumps(doc, allow_nan=False).encode()) > EVIDENCE_MAX_BYTES:
        raise ValueError('audit metric evidence size limit exceeded')
    for path in paths:
        if metric_index.identity(path)[2] > EVIDENCE_MAX_BYTES:
            raise ValueError('audit metric evidence size limit exceeded')
    return paths


def _archive(payload, path):
    archive = json.loads(payload)
    datetime.strptime(path.stem, '%Y-%m-%d')
    if not isinstance(archive, dict) or archive.get('schema') != 1 or archive.get('day') != path.stem:
        raise ValueError('archive schema/day mismatch')
    result = {}
    for kind in retention.RECORDS:
        rows = archive.get(kind, [] if kind == 'errors' else None)
        if not isinstance(rows, list):
            raise ValueError('invalid archive records')
        for row in rows:
            retention._key(row)
            if retention._day(row['time']) != path.stem:
                raise ValueError('archive record is in the wrong UTC day')
        result[kind] = retention._merge([], rows, kind)
    return result


def _read_archive(path, remaining):
    fingerprint = metric_index.identity(path)
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('audit metric archive is unsafe')
        payload = stream.read(remaining + 1)
    if len(payload) > remaining:
        raise ValueError('audit metric evidence size limit exceeded')
    if metric_index.identity(path) != fingerprint:
        raise ValueError('audit metric archive changed during read')
    return _archive(payload, path), len(payload)


def _windows(doc, end, windows):
    result = list(windows) if windows is not None else [(doc['start_at'], end)]
    for begin, stop in result:
        if retention._timestamp(begin) > retention._timestamp(stop) or stop > end:
            raise ValueError('invalid audit metric observation window')
    return result


def _merge_events(doc, indexed, end):
    events = list(doc.get('events', [])) + indexed
    for arm in ARMS:
        events.extend(dict(event, account=arm)
                      for event in doc.get('accounts', {}).get(arm, {}).get('events', []))
    merged = retention._merge([], [row for row in events if row['time'] <= end], 'events')
    identities = {}
    for event in merged:
        identity = (event['time'], event.get('account'), event.get('type'), event.get('symbol'))
        encoded = retention._key(event)
        if identity in identities and identities[identity] != encoded:
            raise ValueError('conflicting metric events')
        identities[identity] = encoded
    return merged


def _ranges(events, windows, end):
    ranges = list(windows)
    closed, _ = trade_metrics.lifecycles(events, end)
    for begin, stop in windows:
        for arm in ARMS:
            selected = [t for t in closed[arm] if begin <= t['closed_at'] <= stop]
            ranges.extend((t['opened_at'], t['closed_at'])
                          for t in selected[-MAX_REPORTED_TRADES:] if t['opened_at'] is not None)
            # Every baseline close needs its initial observation for cohort classification.
            if arm == 'baseline':
                ranges.extend((t['opened_at'], t['opened_at']) for t in selected
                              if t['opened_at'] is not None)
    return ranges


def _needed(path, ranges):
    return any(retention._day(begin) <= path.stem <= retention._day(end)
               for begin, end in ranges)


def _indexes(paths, descriptor, windows):
    indexes, retained = {}, {}
    for path in paths:
        fingerprint = metric_index.identity(path)
        index = metric_index.read(descriptor, path.name, fingerprint, EVIDENCE_MAX_BYTES)
        if index is None:
            archive, _ = _read_archive(path, EVIDENCE_MAX_BYTES)
            if metric_index.identity(path) != fingerprint:
                raise ValueError('audit metric archive changed during indexing')
            index = metric_index.write(descriptor, path.name, fingerprint, archive)
            if _needed(path, windows):
                retained[path] = archive
        indexes[path] = index
    return indexes, retained


def _boundary_days(indexes, windows):
    days = set()
    for start, _ in windows:
        for arm in ARMS:
            prior = [(row['equity_times'][arm], path) for path, row in indexes.items()
                     if arm in row['equity_times'] and row['equity_times'][arm] <= start]
            if prior:
                days.add(max(prior)[1])
    return days


def _select_observations(rows, ranges, windows):
    selected = {row['time'] for row in rows
                if any(begin <= row['time'] <= stop for begin, stop in ranges)}
    for start, _ in windows:
        for arm in ARMS:
            prior = [row['time'] for row in rows if row['time'] <= start
                     and row.get('telemetry_schema') == 1 and not row.get('skipped')
                     and isinstance(row.get('equity'), dict)
                     and isinstance(row['equity'].get(arm), dict)
                     and engine._number(row['equity'][arm].get('equity'))]
            if prior:
                selected.add(max(prior))
    return [row for row in rows if row['time'] in selected]


def _observations(paths, indexes, retained, doc, ranges, windows, end):
    rows = {kind: list(doc.get(kind, [])) for kind in ('observations', 'errors')}
    boundary = _boundary_days(indexes, windows)
    for path in paths:
        if path not in boundary and not _needed(path, ranges):
            continue
        archive = retained.get(path)
        if archive is None:
            archive, _ = _read_archive(path, EVIDENCE_MAX_BYTES)
        if metric_index.identity(path) != indexes[path]['identity']:
            raise ValueError('audit metric archive changed during reporting')
        for kind in rows:
            rows[kind].extend(archive[kind])
    merged = {kind: retention._merge([], [r for r in records if r['time'] <= end], kind)
              for kind, records in rows.items()}
    merged['observations'] = _select_observations(merged['observations'], ranges, windows)
    merged['errors'] = [r for r in merged['errors']
                        if any(begin <= r['time'] <= stop for begin, stop in windows)]
    return merged


def load(runtime, doc, end, *, windows=None):
    """Load full event lifetime and only needed observation days.

    Each archive is parsed once to establish a private event index, then only
    changed archives and report/lifecycle observation days are parsed. Limits
    apply per input file, never to cumulative run age or archive bytes. Cold
    index creation is linear in history; unchanged warm reports are not.
    """
    paths = _paths(runtime, doc, end)
    windows = _windows(doc, end, windows)
    with metric_index.directory(Path(runtime)) as descriptor:
        indexes, retained = _indexes(paths, descriptor, windows)
    indexed = [event for index in indexes.values() for event in index['events']]
    events = _merge_events(doc, indexed, end)
    ranges = _ranges(events, windows, end)
    rows = _observations(paths, indexes, retained, doc, ranges, windows, end)
    for path, index in indexes.items():
        if metric_index.identity(path) != index['identity']:
            raise ValueError('audit metric archive changed during reporting')
    return dict(doc, events=events, **rows)
