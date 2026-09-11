"""Deterministic, trailing-only features from seven days of closed minute OHLC.

EMA slope compares the EMA at the start/end of each window, normalized by
that window's high-low span. Structure compares the extrema of its two halves.
ATR is the last 14 true ranges; typical movement is median 1m high-low over 24h.
Callers must supply chronological, completed candles (never the open candle).
"""
import math
from statistics import median


def _sign(value):
    return (value > 0) - (value < 0)


def _window(bars):
    high = max(bar['high'] for bar in bars)
    low = min(bar['low'] for bar in bars)
    span = high-low
    alpha = 2 / (min(60, len(bars)) + 1)
    ema = bars[0]['close']
    start = ema
    for bar in bars[1:]:
        ema += alpha * (bar['close']-ema)
    halfway = len(bars)//2
    first, last = bars[:halfway], bars[halfway:]
    higher_high = _sign(max(b['high'] for b in last)-max(b['high'] for b in first))
    higher_low = _sign(min(b['low'] for b in last)-min(b['low'] for b in first))
    return {'low': low, 'high': high,
            'position': (bars[-1]['close']-low)/span if span else .5,
            'ema_slope': max(-1., min(1., (ema-start)/span)) if span else 0.,
            'structure': (higher_high+higher_low)/2}


def features(candles, funding_rate=0.):
    """Return reusable setup features, or ``valid=False`` with an exact reason."""
    if len(candles) < 7*24*60:
        return {'valid': False, 'reason': 'Seven days of completed 1-minute candles required'}
    try:
        bars = [{key: float(bar[key]) for key in ('open', 'high', 'low', 'close')}
                for bar in candles[-10080:]]
        funding_rate = float(funding_rate)
        if not math.isfinite(funding_rate):
            raise ValueError('Nonfinite funding rate')
        for bar in bars:
            if not all(math.isfinite(value) and value > 0 for value in bar.values()):
                raise ValueError('OHLC must be finite and positive')
            if not bar['low'] <= min(bar['open'], bar['close']) <= max(bar['open'], bar['close']) <= bar['high']:
                raise ValueError('Invalid OHLC ordering')
        for key in ('timestamp_ms', 'time_ms', 'timestamp', 'time', 'ts'):
            if key in candles[-1]:
                times = [float(bar[key]) for bar in candles[-10080:]]
                delta = times[-1]-times[-2]
                if delta not in (60, 60000) or any(b-a != delta for a,b in zip(times,times[1:])):
                    raise ValueError('History must contain consecutive 1-minute candles')
                break
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        return {'valid': False, 'reason': str(exc)}
    result = {'valid': True, 'price': bars[-1]['close'], 'funding_rate': funding_rate,
              'funding_sign': _sign(funding_rate), 'history_minutes': len(bars)}
    for label, count in (('4h', 240), ('24h', 1440), ('7d', 10080)):
        window = _window(bars[-count:])
        for name, value in window.items():
            result[f'{name}_{label}'] = value
    result['atr_1m'] = sum(max(bars[i]['high']-bars[i]['low'],
                                      abs(bars[i]['high']-bars[i-1]['close']),
                                      abs(bars[i]['low']-bars[i-1]['close']))
                              for i in range(len(bars)-14,len(bars)))/14
    result['typical_movement_1m'] = median(bar['high']-bar['low'] for bar in bars[-1440:])
    result['fresh_high'] = bars[-1]['close'] >= max(bar['high'] for bar in bars[-1440:-1])
    result['fresh_low'] = bars[-1]['close'] <= min(bar['low'] for bar in bars[-1440:-1])
    return result
