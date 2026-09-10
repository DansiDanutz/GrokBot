"""One bounded paper cycle; intended for the Grok Bot native routine scheduler."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time

# Import only the isolated package, never the application's startup module.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from paper_grid import engine, market

DEFAULT_RUNTIME = Path.home() / 'Sandbox/grokbot/zmarty-paper-runtime'


def atomic_json(path, data):
    """Write a complete JSON document or leave the previous one intact."""
    payload = json.dumps(data, indent=2, allow_nan=False) + '\n'
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'w') as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def locked(runtime):
    runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (runtime / '.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('another paper cycle holds the lock') from None
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def config_fingerprint(config):
    return hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()


def load(runtime, config):
    path = runtime / 'account.json'
    if not path.exists():
        return None
    # Invalid/changed state is an error, never an implicit fresh balance.
    data = json.loads(path.read_text())
    if (data.get('schema') != 1 or data.get('config_hash') != config_fingerprint(config)
            or data.get('state', {}).get('mode') != 'paper'):
        raise ValueError('paper state schema/config mismatch; preserve and review state')
    return data


def summary(envelope, now):
    result = engine.status(envelope['state'], envelope.get('market', {}), now,
                           envelope['config'])
    analyses = []
    for symbol, record in envelope.get('market', {}).items():
        analyses.append(dict(symbol=symbol, score=record.get('score'),
            signal_eligible=record.get('eligible', False),
            add_signal_eligible=record.get('add_eligible', False),
            reasons=record.get('reasons', []), bid=record.get('bid'), ask=record.get('ask'),
            quote_age_seconds=round(now-record.get('quote_time', 0), 2),
            atr_pct=record.get('atr_pct'), entry_edge_pct=record.get('entry_edge_pct'),
            range_position=record.get('range_position'), spread_pct=record.get('spread_pct'),
            funding_rate=record.get('funding_rate'), lot_size=record.get('lot_size'),
            multiplier=record.get('multiplier'), ask_size=record.get('ask_size'),
            bid_size=record.get('bid_size')))
    result.update(paused=envelope.get('paused', False), candidate_analysis=analyses,
                  unpaid_liabilities=envelope['state'].get('unpaid_liabilities', 0),
                  checked_at=envelope.get('checked_at'),
                  last_success_at=envelope.get('last_success_at'),
                  top_five=envelope.get('scan', {}).get('top_five',
                            envelope.get('scan', {}).get('top5', [])),
                  scan=envelope.get('scan', {}),
                  recent_events=envelope.get('events', [])[-20:],
                  current_events=envelope.get('current_events', []),
                  label='PAPER ONLY — experimental; USDT treated as USD equivalent',
                  limitations=['Polling observations, not continuous exchange fills',
                               'Funding and adverse fills are modeled',
                               'Strategy effectiveness is unvalidated'])
    return result


def write_report(runtime, result):
    atomic_json(runtime / 'latest.json', result)
    rows = ['# Zmarty paper account', '', result['label'], '',
            f"Checked (epoch seconds): {result.get('checked_at')}",
            f"Paused: {result['paused']}",
            f"Equity: {result['equity']:.4f} USDT",
            f"Free simulated cash: {result['cash']:.4f} USDT",
            f"Realized net PnL: {result['realized_pnl']:.4f} USDT",
            f"Unrealized net PnL: {result['unrealized_net_pnl']:.4f} USDT",
            f"Daily net PnL: {result['daily_pnl']:.4f} USDT",
            f"Open positions: {len(result['positions'])}/2",
            f"Daily halt: {result['halted_day']}",
            f"Unpriced positions: {result['unpriced_positions']}", '',
            '## Current positions', '', '```json',
            json.dumps(result['positions'], indent=2), '```', '',
            '## Discovery and eligibility', '', '```json',
            json.dumps(result['scan'], indent=2), '```', '',
            '## Candidate analysis (execution still subject to engine gates)', '', '```json',
            json.dumps(result['candidate_analysis'], indent=2), '```', '',
            '## Recent paper events', '', '```json',
            json.dumps(result['recent_events'], indent=2), '```', '',
            'No authenticated exchange orders, account keys or production DB writes.',
            'Targets and stops execute only at observed quotes; gaps can exceed loss thresholds.',
            'The $1 target is for the whole position after modeled costs, not every order.', '']
    (runtime / 'latest.md').write_text('\n'.join(rows))


def run_cycle(runtime, force_scan=False, collector=None, now=None):
    runtime = Path(runtime)
    collector = collector or market.collect
    config = engine.default_config()
    with locked(runtime):
        began = time.time() if now is None else now
        envelope = load(runtime, config)
        if envelope and envelope.get('paused'):
            result = summary(envelope, began)
            result['current_events'] = []
            return result
        if envelope and began <= envelope['state'].get('last_run', 0):
            result = summary(envelope, began)
            result['current_events'] = []
            return result
        prior = envelope['scan'] if envelope else None
        held = list(envelope['state']['positions']) if envelope else []
        quotes, scan = collector(prior, held, began, force_scan=force_scan)
        # Collection takes time; fresh quotes must be judged at decision time.
        decision_at = time.time() if now is None else now
        if envelope is None:
            envelope = dict(schema=1, config=config,
                            config_hash=config_fingerprint(config),
                            state=engine.initial_state(config, decision_at),
                            paused=False, events=[], scan={}, market={})
        state, events = engine.step(envelope['state'], quotes, decision_at, config)
        envelope.update(state=state, scan=scan, market=quotes,
                        checked_at=decision_at, current_events=events)
        envelope['events'] = (envelope['events'] + events)[-6000:]
        if quotes and any(isinstance(r, dict) and
                          0 <= decision_at - r.get('quote_time', 0) <= config['max_quote_age']
                          for r in quotes.values()):
            envelope['last_success_at'] = decision_at
        # Account plus events commit together; a replay after a crash cannot double-settle.
        atomic_json(runtime / 'account.json', envelope)
        observation_dir = runtime / 'observations'
        observation_dir.mkdir(exist_ok=True, mode=0o700)
        observation = dict(observed_at=decision_at, market=quotes, scan=scan,
                           events=events, config_hash=envelope['config_hash'])
        atomic_json(observation_dir / (str(int(decision_at * 1000)) + '.json'), observation)
        result = summary(envelope, decision_at)
        write_report(runtime, result)
        return result


def compact(result):
    return {k: result.get(k) for k in ('label', 'paused', 'checked_at', 'last_success_at',
            'cash', 'equity', 'equity_is_estimate', 'realized_pnl', 'unrealized_net_pnl',
            'daily_pnl', 'halted_day', 'unpriced_positions', 'insufficient_exit_depth',
            'unpaid_liabilities', 'positions', 'scan', 'candidate_analysis',
            'current_events')}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['cycle', 'scan', 'status', 'pause', 'resume'])
    parser.add_argument('--paper', action='store_true', help='required for state changes')
    parser.add_argument('--runtime', type=Path, default=DEFAULT_RUNTIME)
    args = parser.parse_args(argv)
    if args.command != 'status' and not args.paper:
        parser.error('state-changing commands require --paper; live mode does not exist')
    try:
        if args.command in ('cycle', 'scan'):
            result = run_cycle(args.runtime, force_scan=args.command == 'scan')
        else:
            with locked(args.runtime):
                envelope = load(args.runtime, engine.default_config())
                if envelope is None:
                    raise ValueError('paper account does not exist; run cycle --paper first')
                if args.command in ('pause', 'resume'):
                    envelope['paused'] = args.command == 'pause'
                    atomic_json(args.runtime / 'account.json', envelope)
                result = summary(envelope, time.time())
                write_report(args.runtime, result)
        print(json.dumps(compact(result), indent=2, allow_nan=False))
        return 0
    except Exception as error:
        # Operational errors never print local environment or credentials.
        print(json.dumps({'mode': 'paper', 'error_type': type(error).__name__,
                          'error': str(error)[:300]}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
