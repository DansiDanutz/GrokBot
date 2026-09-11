"""Versioned, allowlisted JSON replay state; persistence belongs to the caller.

No snapshot, connection, callable, pickle, or trusted prepared-history object is
restored. SHA256 detects damaged packets; caller-supplied identity binds the
source revision, detached data digest and preregistration digest.
"""
from dataclasses import fields
import hashlib
import json
import math
import random

from trader.strategies.grid_types import GridConfig, GridState, Position, Order, FillEvent

SCHEMA_VERSION = 1
TAG = '__replay_type__'
TYPES = {cls.__name__: cls for cls in (GridConfig, GridState, Position, Order, FillEvent)}


def encode_value(value):
    """Detach only supported value types without serializing object capabilities."""
    if value is None or type(value) in (bool, int, str):
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError('checkpoint numbers must be finite')
        return value
    if type(value) in TYPES.values():
        return {TAG: type(value).__name__, 'fields': {
            field.name: encode_value(getattr(value, field.name)) for field in fields(value)}}
    if isinstance(value, dict):
        if any(type(key) is not str for key in value):
            raise ValueError('checkpoint mapping keys must be strings')
        encoded = {key: encode_value(item) for key, item in value.items()}
        return {TAG: 'dict', 'items': list(encoded.items())} if TAG in value else encoded
    if isinstance(value, list):
        return [encode_value(item) for item in value]
    if type(value) in (tuple, set):
        items = [encode_value(item) for item in value]
        if type(value) is set:
            items.sort(key=_canonical)
        return {TAG: type(value).__name__, 'items': items}
    raise ValueError('unsupported checkpoint value type: '+type(value).__name__)


def decode_value(value):
    """Restore only explicit data classes and primitive collection types."""
    if value is None or type(value) in (bool, int, str):
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError('checkpoint numbers must be finite')
        return value
    if type(value) is list:
        return [decode_value(item) for item in value]
    if type(value) is not dict:
        raise ValueError('checkpoint must contain JSON values only')
    if TAG not in value:
        return {key: decode_value(item) for key, item in value.items()}
    name = value[TAG]
    if type(name) is not str:
        raise ValueError('checkpoint value tag must be a string')
    if name in TYPES and set(value) == {TAG, 'fields'}:
        cls, data = TYPES[name], value['fields']
        if type(data) is not dict or set(data) != {field.name for field in fields(cls)}:
            raise ValueError('checkpoint data class fields do not match schema')
        return cls(**{key: decode_value(item) for key, item in data.items()})
    if name in ('tuple', 'set', 'dict') and set(value) == {TAG, 'items'}:
        items = value['items']
        if type(items) is not list:
            raise ValueError('checkpoint collection items must be an array')
        if name == 'dict':
            return _decode_mapping(items)
        decoded = [decode_value(item) for item in items]
        return tuple(decoded) if name == 'tuple' else set(decoded)
    raise ValueError('unknown checkpoint value tag or fields')


def _decode_mapping(items):
    result = {}
    for pair in items:
        if type(pair) not in (tuple, list) or len(pair) != 2 or type(pair[0]) is not str or pair[0] in result:
            raise ValueError('invalid checkpoint mapping')
        result[pair[0]] = decode_value(pair[1])
    return result


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _digest(value):
    digest = hashlib.sha256()
    encoder = json.JSONEncoder(sort_keys=True, separators=(',', ':'), allow_nan=False)
    for fragment in encoder.iterencode(value):
        digest.update(fragment.encode())
    return digest.hexdigest()


def checkpoint_binding(identity, start, end, mode, options, seed, fixtures):
    if (type(identity) is not dict or set(identity) != {'source', 'data', 'registration'}
            or any(type(value) is not str or not value for value in identity.values())):
        raise ValueError('checkpoint identity requires source, data and registration strings')
    return encode_value(dict(identity=identity, start_ms=start, end_ms=end, mode=mode,
                             parameters=options, seed=seed, fixtures=fixtures))


def make_checkpoint(report, execution, binding):
    raw = {key: value for key, value in report.items() if key != '_scan_cache'}
    previous = report.get('_scan_cache')
    cache = None if previous is None else dict(key=previous[0], result=previous[1],
                                              pairs=[row['pair'] for row in previous[2]])
    packet = dict(schema_version=SCHEMA_VERSION, binding=binding,
                  cursor_ms=execution['cursor_ms'], complete=execution['complete'],
                  payload=encode_value(dict(report=raw, execution=execution, scan_cache=cache)))
    packet['sha256'] = _digest(packet)
    return packet


def restore_checkpoint(packet, binding):
    required = {'schema_version', 'binding', 'cursor_ms', 'complete', 'payload', 'sha256'}
    if type(packet) is not dict or set(packet) != required or type(packet['schema_version']) is not int or packet['schema_version'] != SCHEMA_VERSION:
        raise ValueError('invalid or truncated checkpoint schema')
    if packet['binding'] != binding:
        raise ValueError('checkpoint binding differs from requested replay identity or inputs')
    if packet['sha256'] != _digest({key: value for key, value in packet.items() if key != 'sha256'}):
        raise ValueError('checkpoint integrity mismatch')
    try:
        decoded = decode_value(packet['payload'])
        _validate_state(decoded, packet, decode_value(binding))
    except (KeyError, TypeError, AttributeError) as error:
        raise ValueError('invalid checkpoint state') from error
    return decoded['report'], decoded['execution'], decoded['scan_cache']


def _validate_state(decoded, packet, binding):
    if type(decoded) is not dict or set(decoded) != {'report', 'execution', 'scan_cache'}:
        raise ValueError('invalid checkpoint payload')
    report, control, cursor = decoded['report'], decoded['execution'], packet['cursor_ms']
    _validate_control(control)
    if (type(cursor) is not int or cursor % 3600000 or not binding['start_ms'] <= cursor <= binding['end_ms']
            or type(packet['complete']) is not bool or control['cursor_ms'] != cursor
            or control['complete'] != packet['complete']):
        raise ValueError('invalid checkpoint cursor')
    for key in ('start_ms', 'end_ms', 'parameters', 'mode'):
        if report[key] != binding[key]:
            raise ValueError('checkpoint report binding mismatch')
    if report['completed_hours'] != (cursor-binding['start_ms'])/3600000:
        raise ValueError('checkpoint completed hours disagree with cursor')
    if not packet['complete'] and (cursor == binding['end_ms'] or report.get('failed_liquidation')):
        raise ValueError('terminal checkpoint cannot resume execution')
    if packet['complete'] and cursor < binding['end_ms'] and not (report.get('failed_liquidation')
            or not report['coverage']['window_available']):
        raise ValueError('checkpoint cannot finish before its original window ends')
    for bot in report['bots']:
        if type(bot['state']) is not GridState or type(bot['state'].config) is not GridConfig or type(bot['seen']) is not set:
            raise ValueError('checkpoint requires complete unsummarized bot state')
        if bot['state'].timestamp_ms > cursor:
            raise ValueError('checkpoint bot is ahead of cursor')
    cache = decoded['scan_cache']
    if cache is not None and not binding['start_ms'] <= cache['key'][0] <= cursor:
        raise ValueError('checkpoint cache is ahead of cursor')


def _validate_control(control):
    if type(control) is not dict or set(control) != {'cursor_ms', 'cash', 'allowed', 'rng_state', 'complete'}:
        raise ValueError('invalid checkpoint execution state')
    if type(control['cash']) not in (int, float) or not math.isfinite(control['cash']):
        raise ValueError('invalid checkpoint cash')
    if type(control['complete']) is not bool:
        raise ValueError('invalid checkpoint completion flag')
    allowed = control['allowed']
    if allowed is not None and (type(allowed) is not list or any(type(pair) is not str for pair in allowed)):
        raise ValueError('invalid checkpoint allowed symbols')
    try:
        random.Random().setstate(control['rng_state'])
    except (ValueError, TypeError, IndexError) as error:
        raise ValueError('invalid checkpoint RNG state') from error


def restore_scan_cache(snapshot, report, cache):
    """Remint causal records through the reader; never trust serialized preparation."""
    if cache is None:
        return
    at, pairs = cache['key'][0], cache['pairs']
    records = snapshot.records(at, pairs=pairs) if pairs else []
    indexed = {row['pair']: row for row in records}
    if set(indexed) != set(pairs):
        raise ValueError('checkpoint cache membership differs from supplied snapshot')
    report['_scan_cache'] = cache['key'], cache['result'], [indexed[pair] for pair in pairs]
