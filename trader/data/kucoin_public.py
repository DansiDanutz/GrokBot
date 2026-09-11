"""Anonymous fixed-host KuCoin Classic Futures GET client; no trading surface."""
import json
import math
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from trader.data.ratelimit import PUBLIC_LIMITER

BASE = 'https://api-futures.kucoin.com'
INTERVALS = {'1m': 60000, '1h': 3600000}
MAX_BODY = 2000000
MAX_CANDLES = 500
PATH_WEIGHTS = {'/api/v1/contracts/active': 3, '/api/v1/kline/query': 3,
                '/api/v1/contract/funding-rates': 5, '/api/v1/ticker': 2,
                '/api/v1/level2/depth20': 5}


class ProtocolError(ValueError):
    """Sanitized public transport/protocol failure without response content."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        raise ProtocolError('public API redirect rejected')


def _open_public(request, timeout):
    # Separate opener/handlers avoid shared mutable transport state between workers.
    return build_opener(_NoRedirect()).open(request, timeout=timeout)


def symbol_name(symbol):
    if not (isinstance(symbol, str) and 2 <= len(symbol) <= 40
            and symbol == symbol.upper()
            and all(char.isalnum() or char == '_' for char in symbol)):
        raise ValueError('invalid contract symbol')
    return symbol


def number(value, positive=False):
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ProtocolError('invalid numeric public field')
    try:
        result = float(value)
    except (ValueError, OverflowError):
        raise ProtocolError('invalid numeric public field') from None
    if not math.isfinite(result) or (positive and result <= 0):
        raise ProtocolError('invalid numeric public field')
    return result


def integer(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ProtocolError('invalid timestamp or integer field')
    return value


def _window(start_ms, end_ms):
    integer(start_ms); integer(end_ms)
    if end_ms <= start_ms:
        raise ValueError('invalid time window')


def _decode(raw):
    if len(raw) > MAX_BODY:
        raise ProtocolError('public response exceeds size bound')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ProtocolError('duplicate JSON object key')
            result[key] = value
        return result
    try:
        doc = json.loads(raw, object_pairs_hook=unique)
    except (UnicodeError, ValueError, RecursionError):
        raise ProtocolError('invalid public JSON response') from None
    if not isinstance(doc, dict) or doc.get('code') != '200000' or 'data' not in doc:
        raise ProtocolError('public API returned non-success response')
    return doc['data']


class PublicClient:
    def __init__(self, limiter=None, opener=None, sleep=time.sleep, timeout=20, deadline=None, clock=time.monotonic):
        if not 0 < timeout <= 60:
            raise ValueError('invalid timeout')
        self.limiter = limiter or PUBLIC_LIMITER
        self.opener = opener or _open_public
        self.sleep, self.timeout = sleep, timeout
        self.deadline, self.clock = deadline, clock

    def _get(self, path, params=None):
        if path not in PATH_WEIGHTS:
            raise ValueError('public endpoint not allowed')
        url = BASE + path + ('?' + urlencode(params) if params else '')
        for attempt in range(3):
            self._acquire(PATH_WEIGHTS[path])
            request = Request(url, headers={'Accept': 'application/json',
                                           'User-Agent': 'DansLab-public-data/2'}, method='GET')
            try:
                with self.opener(request, timeout=self._timeout()) as response:
                    self.limiter.feedback(response.headers)
                    if response.status != 200:
                        raise ProtocolError('unexpected public HTTP status')
                    return self._read(response)
            except HTTPError as error:
                self.limiter.feedback(error.headers or {}, error.code == 429)
                error.close()
                if error.code != 429 and error.code not in (500, 502, 503, 504):
                    raise ProtocolError('public HTTP request rejected') from None
            except (URLError, TimeoutError, OSError):
                pass
            if attempt < 2:
                delay = 2 ** attempt
                if self.deadline is not None and self.clock() + delay >= self.deadline:
                    raise ProtocolError("public request deadline reached")
                self.sleep(delay)
        raise ProtocolError('public request retries exhausted')

    def _read(self, response):
        chunks = []
        length = 0
        reader = getattr(response, "read1", response.read)
        while True:
            self._timeout()
            chunk = reader(min(65536, MAX_BODY + 1 - length))
            self._timeout()
            if not chunk:
                break
            chunks.append(chunk)
            length += len(chunk)
            if length > MAX_BODY:
                raise ProtocolError('public response exceeds size bound')
        data = _decode(b''.join(chunks))
        self._timeout()
        return data

    def _timeout(self):
        remaining = self.timeout if self.deadline is None else self.deadline - self.clock()
        if remaining <= 0:
            raise ProtocolError('public request deadline reached')
        return min(self.timeout, remaining)

    def _acquire(self, weight):
        self._timeout()
        try:
            if self.deadline is None:
                self.limiter.acquire(weight)
            else:
                self.limiter.acquire(weight, deadline=self.deadline)
        except TimeoutError:
            raise ProtocolError('public request deadline reached') from None

    def contracts(self):
        data = self._get('/api/v1/contracts/active')
        if not isinstance(data, list) or len(data) > 5000:
            raise ProtocolError('invalid contracts response')
        return data

    def klines(self, symbol, interval, start_ms, end_ms):
        symbol_name(symbol); _window(start_ms, end_ms)
        step = INTERVALS.get(interval)
        if step is None or end_ms - start_ms > MAX_CANDLES * step:
            raise ValueError('unsupported interval or oversized candle window')
        data = self._get('/api/v1/kline/query', {'symbol': symbol,
                         'granularity': step // 60000, 'from': start_ms, 'to': end_ms - 1})
        if not isinstance(data, list) or len(data) > MAX_CANDLES:
            raise ProtocolError('invalid candles response')
        rows = [_candle(row, symbol, interval, start_ms, end_ms) for row in data]
        return _unique(rows, 'time_ms')

    def funding(self, symbol, start_ms, end_ms):
        symbol_name(symbol); _window(start_ms, end_ms)
        data = self._get('/api/v1/contract/funding-rates', {
            'symbol': symbol, 'from': start_ms, 'to': end_ms - 1})
        if not isinstance(data, list) or len(data) > 1000:
            raise ProtocolError('invalid funding response')
        rows = []
        for row in data:
            if not isinstance(row, dict) or row.get('symbol') != symbol:
                raise ProtocolError('invalid funding symbol')
            at = integer(row.get('timepoint'))
            if not start_ms <= at < end_ms:
                raise ProtocolError('funding outside requested window')
            rows.append({'symbol': symbol, 'time_ms': at, 'rate': number(row.get('fundingRate'))})
        return _unique(rows, 'time_ms')

    def ticker(self, symbol):
        data = self._get('/api/v1/ticker', {'symbol': symbol_name(symbol)})
        _snapshot(data, symbol)
        for key in ('price', 'bestBidPrice', 'bestAskPrice'):
            number(data.get(key), positive=True)
        for key in ('size', 'bestBidSize', 'bestAskSize'):
            if number(data.get(key)) < 0:
                raise ProtocolError('negative ticker size')
        if number(data['bestBidPrice']) > number(data['bestAskPrice']):
            raise ProtocolError('crossed ticker book')
        return data

    def book(self, symbol):
        data = self._get('/api/v1/level2/depth20', {'symbol': symbol_name(symbol)})
        _snapshot(data, symbol)
        for side in ('bids', 'asks'):
            rows = data.get(side)
            if not isinstance(rows, list) or not 1 <= len(rows) <= 20:
                raise ProtocolError('invalid order book depth')
            prior = None
            for row in rows:
                if not isinstance(row, list) or len(row) != 2:
                    raise ProtocolError('invalid book row')
                price, size = [number(x, positive=True) for x in row]
                if prior is not None and ((side == 'bids' and price >= prior)
                                          or (side == 'asks' and price <= prior)):
                    raise ProtocolError('non-monotonic order book')
                prior = price
        if number(data['bids'][0][0]) > number(data['asks'][0][0]):
            raise ProtocolError('crossed order book')
        return data


def _snapshot(data, symbol):
    if not isinstance(data, dict) or data.get('symbol') != symbol:
        raise ProtocolError('invalid snapshot symbol')
    integer(data.get('ts'))


def _candle(row, symbol, interval, start, end):
    if not isinstance(row, list) or len(row) != 7:
        raise ProtocolError('invalid candle row shape')
    at = integer(row[0])
    if at % INTERVALS[interval] or not start <= at < end:
        raise ProtocolError('candle timestamp outside aligned window')
    opened, high, low, closed = [number(x, positive=True) for x in row[1:5]]
    volume, turnover = [number(x) for x in row[5:]]
    if low > min(opened, closed) or high < max(opened, closed) or low > high:
        raise ProtocolError('invalid candle price range')
    if volume < 0 or turnover < 0:
        raise ProtocolError('negative candle volume')
    return dict(symbol=symbol, interval=interval, time_ms=at, open=opened,
                high=high, low=low, close=closed, volume=volume, turnover=turnover)


def _unique(rows, key):
    found = {}
    for row in rows:
        previous = found.get(row[key])
        if previous is not None and previous != row:
            raise ProtocolError('conflicting duplicate public record')
        found[row[key]] = row
    return [found[key] for key in sorted(found)]


def select_contracts(rows, symbols=None):
    requested = None if symbols is None else {symbol_name(x) for x in symbols}
    selected = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ProtocolError('invalid contract record')
        if not (row.get('status') == 'Open' and row.get('quoteCurrency') == 'USDT'
                and row.get('settleCurrency') == 'USDT' and row.get('isInverse') is False
                and row.get('type') == 'FFWCSX' and row.get('expireDate') is None
                and row.get('settleDate') is None
                and row.get('assetClass', row.get('marketType')) == 'CRYPTO'):
            continue
        symbol = symbol_name(row.get('symbol'))
        if requested is not None and symbol not in requested:
            continue
        integer(row.get('firstOpenDate'))
        number(row.get('multiplier'), positive=True)
        number(row.get('lotSize'), positive=True)
        if symbol in selected and selected[symbol] != row:
            raise ProtocolError('conflicting duplicate contract')
        selected[symbol] = row
    if requested is not None and requested != selected.keys():
        raise ProtocolError('requested symbol outside active crypto USDT perpetual universe')
    return [selected[key] for key in sorted(selected)]
