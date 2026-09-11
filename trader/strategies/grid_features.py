"""Deterministic, trailing-only features from seven days of closed minute OHLC.

EMA slope compares the EMA at the start/end of each window, normalized by
that window's high-low span. Structure compares the extrema of its two halves.
ATR is the last 14 true ranges; typical movement is median 1m high-low over 24h.
Raw timestamped history may have up to 5% gaps, forward-filled for indicators.
Prepared history carries its original coverage and synthetic flags. Only completed
candles enter the trailing window; missing leading prices require a prior seed.
"""
import math
from statistics import median

from trader.strategies.candle_coverage import prepare


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


def _swing_structure(bars):
    """Five-bar extrema require two CLOSED candles on each side to confirm."""
    evidence = {'method':'confirmed five-bar swing pivots; two closed bars on each side',
                'confirmation_bars':2, 'lookback_minutes':len(bars)}
    for label, key, comparison in (('support','low',min),('resistance','high',max)):
        levels = {}
        count = 0
        for index in range(2,len(bars)-2):
            if any(bar.get('synthetic',False) for bar in bars[index-2:index+3]):
                continue
            price = bars[index][key]
            left = [bars[i][key] for i in (index-2,index-1)]
            right = [bars[i][key] for i in (index+1,index+2)]
            if price != comparison(left+[price]+right):
                continue
            # A plateau alone is not evidence of a reversal.
            if not any(value != price for value in left) or not any(value != price for value in right):
                continue
            count += 1
            level = levels.setdefault(price,{'price':price,'count':0,'count_4h':0,'count_24h':0,
                                             'last_confirmed_index':index+2})
            level['count'] += 1
            level['count_4h'] += int(index+2 >= len(bars)-240)
            level['count_24h'] += int(index+2 >= len(bars)-1440)
            level['last_confirmed_index'] = index+2
        evidence[f'{label}s'] = [levels[price] for price in sorted(levels)]
        evidence[f'{label}_pivot_count'] = count
    return evidence


def _prepare_features(candles, asof_ms, prior_seed):
    if isinstance(candles,dict):
        return candles
    if not candles:
        return prepare([],asof_ms or 10080*60000,prior_seed=prior_seed)
    timestamp_key = next((key for key in ('timestamp_ms','time_ms','timestamp','time','ts')
                          if key in candles[-1]),None)
    if timestamp_key is None:
        if len(candles) < 10080:
            return {'valid':False,'reason':'Seven days of completed 1-minute candles required'}
        # Backward-compatible OHLC-only callers already promise dense closed bars.
        normalized = [dict(bar,timestamp_ms=index*60000) for index,bar in enumerate(candles[-10080:])]
        return prepare(normalized,10080*60000)
    if timestamp_key in ('timestamp_ms','time_ms'):
        normalized = candles
        latest = max(int(bar[timestamp_key]) for bar in candles)
    else:
        times = [float(bar[timestamp_key]) for bar in candles]
        positive_steps = [b-a for a,b in zip(times,times[1:]) if b > a]
        milliseconds = max(times) >= 1e11 or positive_steps and min(positive_steps) >= 60000
        scale = 1 if milliseconds else 1000
        if any(not math.isfinite(timestamp) or not (timestamp*scale).is_integer() for timestamp in times):
            raise ValueError('Candle timestamps must be finite integer milliseconds')
        normalized = [dict(bar,timestamp_ms=int(timestamp*scale)) for bar,timestamp in zip(candles,times)]
        latest = max(bar['timestamp_ms'] for bar in normalized)
    return prepare(normalized,asof_ms if asof_ms is not None else latest+60000,prior_seed=prior_seed)


def features(candles, funding_rate=0., asof_ms=None, prior_seed=None):
    """Features from prepared history or raw candles; 95% observed coverage required.

    Gaps are forward-filled by prepare for indicators only. A prepared result keeps
    its original actual/synthetic coverage, and no missing leading price is inferred
    from later data. Unknown funding is an explicitly labelled neutral contribution.
    Without asof_ms, raw timestamped input promises its newest candle is closed.
    """
    prepared = None
    try:
        prepared = _prepare_features(candles,asof_ms,prior_seed)
        if not prepared.get('valid',False):
            return {'valid':False,'reason':prepared.get('reason','Invalid prepared candles'),
                    'coverage':prepared.get('coverage',{})}
        if len(prepared['bars']) != 10080:
            raise ValueError('Features require a seven-day prepared window')
        bars = []
        for original in prepared['bars']:
            bar = {key:float(original[key]) for key in ('open','high','low','close')}
            if not all(math.isfinite(value) and value > 0 for value in bar.values()):
                raise ValueError('Prepared OHLC must be finite and positive')
            if not bar['low'] <= min(bar['open'],bar['close']) <= max(bar['open'],bar['close']) <= bar['high']:
                raise ValueError('Invalid prepared OHLC ordering')
            bar['synthetic'] = original.get('synthetic',False)
            if type(bar['synthetic']) is not bool:
                raise ValueError('Prepared synthetic flag must be boolean')
            bars.append(bar)
        actual = sum(not bar['synthetic'] for bar in bars)
        coverage = prepared['coverage']
        if (coverage['expected'] != 10080 or coverage['actual'] != actual or actual < 9576
                or not coverage['eligible'] or not coverage['indicators_valid']
                or not math.isclose(coverage['fraction'],actual/10080)):
            raise ValueError('Prepared coverage does not match its observed candles')
        funding_unknown = funding_rate is None
        if not funding_unknown:
            funding_rate = float(funding_rate)
            if not math.isfinite(funding_rate):
                raise ValueError('Nonfinite funding rate')
    except (KeyError,TypeError,ValueError,OverflowError) as exc:
        return {'valid':False,'reason':str(exc),'coverage':prepared.get('coverage',{}) if prepared else {}}
    result = {'valid':True,'price':bars[-1]['close'],'funding_rate':funding_rate,
              'funding_sign':0 if funding_unknown else _sign(funding_rate),
              'funding_unknown':funding_unknown,'history_minutes':len(bars),
              'coverage':dict(prepared['coverage'])}
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
    result['support_resistance'] = _swing_structure(bars)
    return result
