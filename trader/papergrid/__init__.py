"""Pure, immutable paper grid accounting. No exchange or filesystem access."""

from trader.papergrid.engine import close_bot, open_bot, step

__all__ = ['open_bot', 'step', 'close_bot']
