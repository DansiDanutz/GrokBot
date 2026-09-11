"""Immutable paper-grid values; quantities are base units, prices are USDT."""
from dataclasses import dataclass


@dataclass(frozen=True)
class GridConfig:
    pair: str
    low: float
    high: float
    grids: int = 20
    leverage: float = 5
    investment: float = 1000
    direction: str = 'neutral'
    trigger: float | None = None
    quantity: float | None = None
    multiplier: float = 1
    lot_size: float = 1
    maintenance_rate: float = .005
    reserved_margin: float = 0
    entry_price: float | None = None
    stop_loss: float | None = None
    stop_loss_high: float | None = None
    tick_size: float | None = None
    range_exit_stop_pct: float | None = .05
    adaptive_range_stops: bool = False
    adaptive_tight_stop_pct: float = .01
    adaptive_liquidation_clearance_pct: float = .01

    @property
    def total_margin(self):
        return self.investment + self.reserved_margin


@dataclass(frozen=True)
class Position:
    slot: int
    side: int
    quantity: float
    entry: float
    seed: bool = False


@dataclass(frozen=True)
class Order:
    slot: int
    side: int
    price: float
    closing: bool


@dataclass(frozen=True)
class FillEvent:
    event_id: int
    timestamp_ms: int
    slot: int
    side: int
    quantity: float
    price: float
    fee: float
    kind: str
    gross_pnl: float = 0
    completed_grid: bool = False


@dataclass(frozen=True)
class GridState:
    config: GridConfig
    price: float
    timestamp_ms: int
    status: str = 'waiting'
    positions: tuple[Position, ...] = ()
    orders: tuple[Order, ...] = ()
    completed_grids: int = 0
    grid_profit: float = 0
    grid_net_profit: float = 0
    max_floating_loss: float = 0
    equity_marks: tuple[float, ...] = ()
    floating_pnl_marks: tuple[float, ...] = ()
    seed_pnl: float = 0
    close_pnl: float = 0
    fees: float = 0
    funding: float = 0
    completion_times: tuple[int, ...] = ()
    range_exits: int = 0
    liquidated: bool = False
    last_funding_ms: int = 0
    stop_reason: str | None = None
    fill_events: tuple[FillEvent, ...] = ()
    fill_sequence: int = 0
    effective_range_exit_stop_pct_low: float | None = None
    effective_range_exit_stop_pct_high: float | None = None
    protective_reason: str | None = None
    adaptive_stop_events: tuple[tuple[int, str, float, str], ...] = ()
