"""Deterministic synthetic-only audit fixture. This module never reads runtime data."""

import json
from pathlib import Path
from paper_grid import audits, coinglass, engine
from paper_grid.metric_constants import ARMS

SAMPLE_START = 1800000000.0
SAMPLE_TICK = 300
SAMPLE_SYMBOLS = ("AAAUSDTM", "BBBUSDTM")
SAMPLE_PRICES = ((100, 100), (98, 100), (99, 101), (103, 94))
SAMPLE_OUTPUT_STEM = "phase-1-sample"


def _quote(symbol, price, at, add):
    return dict(
        symbol=symbol,
        bid=price,
        ask=price * 1.0001,
        mark=price,
        bid_size=1000000,
        ask_size=1000000,
        quote_time=at,
        lot_size=1,
        multiplier=0.001,
        tick_size=0.001,
        funding_rate=0.001,
        funding_interval_hours=8,
        score=70,
        eligible=True,
        add_eligible=add,
        atr_pct=2,
        entry_edge_pct=4,
        turnover24h=10000000,
    )


def _features(at):
    return dict(
        fetched_at=at,
        symbols={
            symbol: dict(
                eligible=index == 1,
                latest_hour=(int(at) // 3600 - 1) * 3600,
                burst_ratio=4,
                long_share=0.7,
                total_usd=100,
            )
            for index, symbol in enumerate(SAMPLE_SYMBOLS)
        },
    )


def document():
    """Return generated fills, quotes and states for two synthetic paper accounts."""
    config = engine.default_config()
    states = {arm: engine.initial_state(config, SAMPLE_START) for arm in ARMS}
    doc = dict(
        schema=1,
        mode="paper",
        start_at=SAMPLE_START,
        config=config,
        tick_seconds=SAMPLE_TICK,
        report_seconds=SAMPLE_TICK,
        events=[],
        observations=[],
        history=[],
        errors=[],
        accounts={},
    )
    for index, prices in enumerate(SAMPLE_PRICES):
        at = SAMPLE_START + (index + 1) * SAMPLE_TICK
        market = {
            symbol: _quote(symbol, price, at, index == 1)
            for symbol, price in zip(SAMPLE_SYMBOLS, prices)
        }
        features = _features(at)
        equity = {}
        for arm in ARMS:
            inputs = (
                coinglass.apply_filter(market, features, at)
                if arm == "liquidation_filter"
                else market
            )
            states[arm], events = engine.step(states[arm], inputs, at, config)
            doc["events"].extend(dict(e, account=arm) for e in events)
            status = engine.status(states[arm], market, at, config)
            equity[arm] = {key: status[key] for key in ("equity", "equity_is_estimate")}
        doc["observations"].append(
            dict(
                time=at,
                market=market,
                coinglass=features,
                scan=dict(scanned_at=at, top5=list(SAMPLE_SYMBOLS), liquid_contracts=2),
                telemetry_schema=1,
                equity=equity,
            )
        )
    doc["last_tick_at"] = at
    doc["accounts"] = {
        arm: dict(state=states[arm], events=[], statistics=dict(initial_equity=1000))
        for arm in ARMS
    }
    return doc


def report():
    doc = document()
    result = audits._build(doc, "audit48h", SAMPLE_START, doc["last_tick_at"], doc["last_tick_at"])
    result["summary"] = (
        "SYNTHETIC FIXTURE ONLY. Generated quotes and paper fills; no market data or profitability claim."
    )
    return result


def write_sample(output_directory):
    """Write fixed synthetic filenames to an explicitly selected evidence directory.

    Example: write_sample(Path('docs/roadmap-evidence').resolve()). No runtime,
    provider, key or default output location is consulted.
    """
    directory = Path(output_directory)
    if any(path.is_symlink() for path in (directory, *directory.parents)) or not directory.is_dir():
        raise ValueError("sample output must be an existing directory without symlinks")
    result = report()
    page, _ = audits._render(result)
    contents = {"json": json.dumps(result, indent=2, allow_nan=False) + "\n", "html": page}
    targets = {
        extension: directory / (SAMPLE_OUTPUT_STEM + "." + extension) for extension in contents
    }
    if any(
        path.is_symlink() or (path.exists() and not path.is_file()) for path in targets.values()
    ):
        raise ValueError("sample output file is unsafe")
    for extension, path in targets.items():
        audits._atomic_text(path, contents[extension])
    return {extension: str(path) for extension, path in targets.items()}
