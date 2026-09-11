"""Conservative process-shared public API budget, with provider backpressure."""
import math
import threading
import time
from email.utils import parsedate_to_datetime


class TokenBucket:
    def __init__(self, rate=15.0, capacity=15.0, clock=time.monotonic,
                 sleep=time.sleep, wall_clock=time.time):
        if not all(math.isfinite(x) and x > 0 for x in (rate, capacity)):
            raise ValueError('rate and capacity must be finite and positive')
        self.rate, self.capacity = rate, capacity
        self.clock, self.sleep, self.wall_clock = clock, sleep, wall_clock
        self.tokens, self.updated, self.blocked_until = capacity, clock(), 0.0
        self.lock = threading.Lock()

    def acquire(self, weight, deadline=None):
        if not math.isfinite(weight) or weight <= 0 or weight > self.capacity:
            raise ValueError('invalid request weight')
        while True:
            with self.lock:
                now = self.clock()
                if deadline is not None and now >= deadline:
                    raise TimeoutError("public request deadline reached")
                self.tokens = min(self.capacity, self.tokens + max(0, now - self.updated) * self.rate)
                self.updated = now
                delay = max(self.blocked_until - now, (weight - self.tokens) / self.rate, 0)
                if delay == 0:
                    self.tokens -= weight
                    return
            if deadline is not None and self.clock() + delay >= deadline:
                raise TimeoutError("public request deadline reached")
            self.sleep(delay)

    def feedback(self, headers, limited=False):
        values = {key.lower(): value for key, value in headers.items()}
        delay = 1.0 if limited else 0.0
        if limited or _number(values.get('gw-ratelimit-remaining'), 1) < 5:
            delay = max(delay, _number(values.get('gw-ratelimit-reset'), 0) / 1000)
        retry = values.get('retry-after')
        if retry:
            try:
                retry_seconds = float(retry)
            except ValueError:
                try:
                    retry_seconds = parsedate_to_datetime(retry).timestamp() - self.wall_clock()
                except (ValueError, TypeError, OverflowError):
                    retry_seconds = 1.0
            if math.isfinite(retry_seconds):
                delay = max(delay, retry_seconds)
        with self.lock:
            self.blocked_until = max(self.blocked_until, self.clock() + max(0, delay))


def _number(value, default):
    try:
        number = float(value)
        return number if math.isfinite(number) and number >= 0 else default
    except (ValueError, TypeError):
        return default


PUBLIC_LIMITER = TokenBucket()
