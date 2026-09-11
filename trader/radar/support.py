"""Deterministic repeated-pivot structure from completed hourly candles."""
from statistics import median


def levels(hourly, price, atr):
    """Two candles confirm either side of each pivot; at least two tests required."""
    pivots = []
    rows = hourly[-168:]
    tolerance = min(atr * .25, price * .005)
    for i in range(2, len(rows)-2):
        neighbors = rows[i-2:i] + rows[i+1:i+3]
        low, high = rows[i][3], rows[i][2]
        if low < min(r[3] for r in neighbors):
            pivots.append((low, rows[i][0], 'low'))
        if high > max(r[2] for r in neighbors):
            pivots.append((high, rows[i][0], 'high'))
    clusters = []
    for pivot in sorted(pivots):
        if not clusters or pivot[0]-clusters[-1][0][0] > tolerance:
            clusters.append([])
        clusters[-1].append(pivot)
    supports, resistances = [], []
    for cluster in clusters:
        times = sorted(set(p[1] for p in cluster))
        if len(times) < 2 or times[-1]-times[0] < 3*3600000:
            continue
        level = median(p[0] for p in cluster)
        if level < price and (any(p[2] == 'low' for p in cluster)
                              or all(r[4] > level for r in rows[-2:])):
            supports.append((level, len(times)))
        if level > price and (any(p[2] == 'high' for p in cluster)
                               or all(r[4] < level for r in rows[-2:])):
            resistances.append((level, len(times)))
    support = max(supports, default=(None, 0))
    resistance = min(resistances, default=(None, 0))
    return dict(support=support[0], support_touches=support[1],
                resistance=resistance[0], resistance_touches=resistance[1])
