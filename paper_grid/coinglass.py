"""Read-only, fail-closed CoinGlass filter for the paper experiment.

This fixed heuristic is unvalidated: a completed hourly liquidation burst at
least 3x its prior-hour mean, with at least 60% long liquidations. It is not a
liquidation heatmap, and its exchanges are not KuCoin execution data.
"""
from __future__ import annotations

import copy
import errno
import json
import math
import os
from pathlib import Path
import re
import stat
import urllib.error
import urllib.parse
import urllib.request

URL = 'https://open-api-v4.coinglass.com/api/futures/liquidation/aggregated-history'
DEFAULT_SECRETS_FILE = '~/.openclaw-secrets/paper-grid.env'
SECRETS_FILE_ENV = 'PAPER_GRID_SECRETS_FILE'
MAX_SECRETS_BYTES = 16_384
PRIVATE_FILE_MODE = 0o600
ICLOUD_CONTAINERS = frozenset({'mobile documents', 'com~apple~clouddocs'})
HOUR = 3600
CACHE_SECONDS = 1800
MAX_HISTORY_AGE = 2 * HOUR
MAX_SYMBOLS = 9
MAX_BODY = 500_000
SYMBOL = re.compile(r'^[A-Z0-9]{1,30}USDTM$')
SOURCE = 'CoinGlass aggregated liquidation history; Binance,OKX,Bybit; 1h'
LIMITATIONS = [
    'Unvalidated fixed hypothesis; no demonstrated predictive edge.',
    'Completed liquidations, not prospective liquidation clusters or heatmaps.',
    'Binance, OKX and Bybit aggregate; not KuCoin execution data.',
    'Missing, stale or invalid data blocks entries and additions, never exits.',
]


class CoinGlassError(ValueError):
    """A sanitized error containing no credentials or provider response body."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise CoinGlassError('redirect refused')


def _number(value):
    if isinstance(value, bool):
        raise CoinGlassError('invalid number')
    try:
        result = float(value)
    except (ValueError, TypeError, OverflowError):
        raise CoinGlassError('invalid number') from None
    if not math.isfinite(result):
        raise CoinGlassError('non-finite number')
    return result


def _clock(value):
    now = _number(value)
    if not 1e9 <= now < 1e10:
        raise CoinGlassError('invalid clock')
    return now


def underlying(symbol):
    if not isinstance(symbol, str) or not SYMBOL.fullmatch(symbol):
        raise CoinGlassError('invalid symbol')
    name = symbol[:-5]
    return 'BTC' if name == 'XBT' else name


def _icloud_path(path):
    parts = tuple(part.casefold() for part in path.parts)
    return (bool(ICLOUD_CONTAINERS.intersection(parts))
            or any(parent == 'cloudstorage' and child.startswith('icloud')
                   for parent, child in zip(parts, parts[1:])))


def secrets_path(path=None):
    value = os.environ.get(SECRETS_FILE_ENV, DEFAULT_SECRETS_FILE) if path is None else path
    if not isinstance(value, (str, Path)) or not str(value).strip():
        raise CoinGlassError('invalid secrets-file configuration')
    try:
        configured = Path(value).expanduser().absolute()
        resolved = configured.resolve()
    except (OSError, RuntimeError, ValueError):
        raise CoinGlassError('invalid secrets-file configuration') from None
    if any(part.casefold() == 'desktop' for part in (*configured.parts, *resolved.parts)):
        raise CoinGlassError('Desktop secrets files are not allowed; use a private local path')
    if any(_icloud_path(candidate) for candidate in (configured, resolved)):
        raise CoinGlassError('iCloud secrets files are not allowed; use a private local path')
    # Retain lexical components so descriptor traversal refuses any symlink.
    return configured


def _open_private_component(name, flags, directory):
    metadata = os.stat(name, dir_fd=directory, follow_symlinks=False)
    if stat.S_ISLNK(metadata.st_mode):
        raise CoinGlassError('path contains a symlink')
    try:
        return os.open(name, flags | os.O_NOFOLLOW, dir_fd=directory)
    except OSError as error:
        if error.errno == errno.ELOOP:
            raise CoinGlassError('path contains a symlink') from None
        raise


def _private_descriptor(path):
    directory = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    descriptor = None
    try:
        for component in path.parts[1:-1]:
            following = _open_private_component(
                component, os.O_RDONLY | os.O_DIRECTORY, directory)
            os.close(directory)
            directory = following
        descriptor = _open_private_component(
            path.name, os.O_RDONLY | os.O_NONBLOCK, directory)
        metadata = os.fstat(descriptor)
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid()
                or stat.S_IMODE(metadata.st_mode) != PRIVATE_FILE_MODE
                or not 0 <= metadata.st_size <= MAX_SECRETS_BYTES):
            raise CoinGlassError('API key unavailable')
        return descriptor
    except BaseException:
        if descriptor is not None:
            os.close(descriptor)
        raise
    finally:
        os.close(directory)


def _secret_text(path):
    try:
        descriptor = _private_descriptor(path)
        with os.fdopen(descriptor, 'rb') as handle:
            payload = handle.read(MAX_SECRETS_BYTES + 1)
        if len(payload) > MAX_SECRETS_BYTES:
            raise CoinGlassError('API key unavailable')
        return payload.decode('utf-8')
    except CoinGlassError:
        raise
    except (OSError, UnicodeError, ValueError):
        raise CoinGlassError('API key unavailable') from None


def read_api_key(path=None):
    """Read a bounded owned 0600 regular file, without following symlinks."""
    text = _secret_text(secrets_path(path))
    found = None
    for line in text.splitlines():
        if line.startswith('COINGLASS_API_KEY='):
            if found is not None:
                raise CoinGlassError('ambiguous API key configuration')
            found = line.split('=', 1)[1].strip()
            if len(found) >= 2 and found[0] == found[-1] and found[0] in ('"', "'"):
                found = found[1:-1]
    return _validate_key(found)


def _validate_key(key):
    if not isinstance(key, str) or not 1 <= len(key) <= 512 or any(ord(c) < 33 or ord(c) > 126 for c in key):
        raise CoinGlassError('API key unavailable')
    return key


def request_history(symbol, now, api_key):
    """One bounded GET to the fixed official endpoint, with redirects refused."""
    params = dict(exchange_list='Binance,OKX,Bybit', symbol=underlying(symbol),
                  interval='1h', limit=49, end_time=int(_clock(now) * 1000))
    request = urllib.request.Request(URL + '?' + urllib.parse.urlencode(params),
                                    headers={'Accept': 'application/json', 'CG-API-KEY': _validate_key(api_key),
                                             'User-Agent': 'ZmartyChat-paper-grid/1'}, method='GET')
    try:
        with urllib.request.build_opener(_NoRedirect).open(request, timeout=5) as response:
            payload = response.read(MAX_BODY + 1)
        if len(payload) > MAX_BODY:
            raise CoinGlassError('response too large')
        result = json.loads(payload)
        if not isinstance(result, dict) or str(result.get('code')) != '0' or not isinstance(result.get('data'), list):
            raise CoinGlassError('history unavailable or subscription restricted')
        return result['data']
    except CoinGlassError:
        raise
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, TypeError):
        raise CoinGlassError('history request failed') from None


def analyze(rows, now):
    now = _clock(now)
    if not isinstance(rows, list) or len(rows) > 49:
        raise CoinGlassError('invalid history size')
    seen, completed = set(), []
    for row in rows:
        try:
            stamp = _number(row['time']) / 1000
            long = _number(row['aggregated_long_liquidation_usd'])
            short = _number(row['aggregated_short_liquidation_usd'])
        except (KeyError, TypeError):
            raise CoinGlassError('invalid history row') from None
        if stamp % HOUR or not 1e9 <= stamp <= now or stamp in seen or min(long, short) < 0:
            raise CoinGlassError('invalid, future or duplicate history row')
        seen.add(stamp)
        total = _number(long + short)
        if stamp + HOUR <= now:
            completed.append((stamp, long, total))
    completed.sort()
    if len(completed) < 25:
        raise CoinGlassError('fewer than 25 completed hours')
    if any(b[0] - a[0] != HOUR for a, b in zip(completed, completed[1:])):
        raise CoinGlassError('gapped history')
    latest, long, total = completed[-1]
    if now - (latest + HOUR) > MAX_HISTORY_AGE:
        raise CoinGlassError('stale completed history')
    prior = completed[-49:-1]
    mean = _number(sum(row[2] / len(prior) for row in prior))
    if total <= 0 or mean <= 0:
        raise CoinGlassError('zero latest liquidation or baseline')
    share, ratio = long / total, _number(total / mean)
    eligible = ratio >= 3 and share >= .60
    reason = 'liquidation_filter_pass' if eligible else ('liquidation_burst_below_3x' if ratio < 3 else 'long_share_below_60pct')
    return dict(eligible=eligible, reason=reason, latest_hour=latest, total_usd=total,
                long_share=share, burst_ratio=ratio, baseline_hours=len(prior), baseline_mean_usd=mean)


def _fresh(envelope, now):
    try:
        return isinstance(envelope, dict) and 0 <= _clock(now) - _clock(envelope['fetched_at']) < CACHE_SECONDS
    except (KeyError, CoinGlassError):
        return False


def collect(symbols, now, cache=None, *, api_key=None, key_path=None, getter=None):
    """Collect at most nine names serially; reuse a covered cache for 30 minutes.

    Optional getter(symbol, now, api_key) returns history rows for offline tests.
    Errors are deliberately generic even for injected transport exceptions.
    """
    now = _clock(now)
    names = list(dict.fromkeys(symbols))
    if _fresh(cache, now) and isinstance(cache.get('symbols'), dict) and all(s in cache['symbols'] for s in names):
        result = copy.deepcopy(cache)
        result['symbols'] = {s: result['symbols'][s] for s in names}
        return result
    result = dict(fetched_at=now, symbols={}, errors=[], source=SOURCE, limitations=list(LIMITATIONS))
    try:
        key = read_api_key(key_path) if api_key is None else _validate_key(api_key)
    except CoinGlassError:
        key = None
    fetch = getter or request_history
    for index, symbol in enumerate(names):
        try:
            underlying(symbol)
            if index >= MAX_SYMBOLS:
                raise CoinGlassError('symbol request limit exceeded')
            if key is None:
                raise CoinGlassError('API key unavailable')
            try:
                rows = fetch(symbol, now, key)
            except Exception:
                raise CoinGlassError('history request failed') from None
            result['symbols'][symbol] = analyze(rows, now)
        except CoinGlassError as error:
            reason = str(error)
            result['symbols'][symbol] = dict(eligible=False, reason=reason, latest_hour=None,
                                            total_usd=None, long_share=None, burst_ratio=None)
            result['errors'].append({'symbol': symbol, 'reason': reason})
    return result


def apply_filter(quotes, features, now):
    """Preserve exit quotes/depth; require fresh valid features for new risk."""
    result = copy.deepcopy(quotes)
    items = result.values() if isinstance(result, dict) else result
    for quote in items:
        allowed, reason = False, 'coinglass_missing_or_stale'
        try:
            if not _fresh(features, now):
                raise CoinGlassError('stale features')
            feature = features['symbols'][quote['symbol']]
            age = _clock(now) - (_clock(feature['latest_hour']) + HOUR)
            ratio, share, total = (_number(feature[k]) for k in ('burst_ratio', 'long_share', 'total_usd'))
            allowed = (feature.get('eligible') is True and 0 <= age <= MAX_HISTORY_AGE
                       and ratio >= 3 and .60 <= share <= 1 and total > 0)
            reason = 'coinglass_filter_not_met' if 0 <= age <= MAX_HISTORY_AGE else 'coinglass_missing_or_stale'
        except (CoinGlassError, KeyError, TypeError):
            pass
        quote['eligible'] = quote.get('eligible') is True and allowed
        quote['add_eligible'] = quote.get('add_eligible') is True and allowed
        if not allowed:
            quote.setdefault('reasons', []).append(reason)
    return result
