"""One reviewed paper-only telemetry seal migration; never resets accounts.

Dry run by default. A write requires --paper and the same exclusive cycle lock.
Only the exact pre-instrumentation source/config seal is eligible. This is not
an escape hatch for arbitrary strategy changes or a frozen experiment.
"""
import argparse
from copy import deepcopy
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from paper_grid import cli, experiment

BASELINE_HASHES = {
    'engine.py': '595278bc8c9d2cafb910eadf6b227ea76f0f480700c5c7a6f3929278b253b6af',
    'market.py': 'c7bef3e1b8916a34508e7b8085f43eaafe7b5df3918f1c6d78fc27656731d9e4',
    'coinglass.py': '78a85ee5485c8f1cb8209744328eb359384904fc64421e43c22c6ef870c25159',
    'experiment.py': 'e69a19846da2371577511bad53f055d2386285b21e77fbd106c0d5d80a5e3244',
}
BASELINE_CONFIG_HASH = '054dc3591383755042f0d9abea9c84da9c0829dfb82e0b026b91b397f2f32c35'
# Fixed to the reviewed instrumentation revision, not whatever happens to be on disk.
TARGET_HASHES = {
    'engine.py': '6b165bf32e566d9a6436239021c3d77636bce404dd2833fb4125c4c0f6f7e07e',
    'market.py': 'c7bef3e1b8916a34508e7b8085f43eaafe7b5df3918f1c6d78fc27656731d9e4',
    'coinglass.py': '78a85ee5485c8f1cb8209744328eb359384904fc64421e43c22c6ef870c25159',
    'experiment.py': 'fe9be3733230938607aaf3e7c41dd4af9d493d30f3f0b8eb1e3b44ba688ebf51',
}


def upgrade(runtime, *, now=None, paper=False):
    runtime = Path(runtime)
    if not runtime.is_dir() or any(p.is_symlink() for p in
                                  (runtime, runtime / experiment.FILE, runtime / '.lock')):
        raise ValueError('regular existing runtime required')
    at = experiment._now(now)
    with cli.locked(runtime):
        doc = experiment._load(runtime)
        current = experiment._code_hashes()
        if current != TARGET_HASHES:
            raise ValueError('source is not the reviewed telemetry revision')
        if (doc.get('status') != 'running' or doc.get('continuous') is not True
                or doc.get('end_at') is not None or doc.get('freeze_reason') is not None
                or doc.get('frozen_at') is not None or doc['config_hash'] != BASELINE_CONFIG_HASH
                or at < (doc.get('last_tick_at') or doc['start_at'])):
            raise ValueError('only unchanged running continuous paper baseline may upgrade')
        boundaries = doc.get('telemetry_upgrades', [])
        if not isinstance(boundaries, list):
            raise ValueError('invalid upgrade history')
        if doc.get('code_hashes') == current:
            if (doc.get('telemetry_schema') == experiment.TELEMETRY_SCHEMA
                    and len(boundaries) == 1
                    and boundaries[0].get('kind') == 'telemetry_only'
                    and boundaries[0].get('old_hashes') == BASELINE_HASHES
                    and boundaries[0].get('new_hashes') == current):
                return dict(mode='paper', status='already_upgraded', written=False)
            raise ValueError('current seal has no recognized upgrade boundary')
        if doc.get('code_hashes') != BASELINE_HASHES or boundaries or doc.get('telemetry_schema') is not None:
            raise ValueError('unknown baseline source seal or telemetry history')
        updated = deepcopy(doc)
        updated.update(code_hashes=current, telemetry_schema=experiment.TELEMETRY_SCHEMA,
                       telemetry_upgrades=[dict(time=at, kind='telemetry_only',
                                                old_hashes=BASELINE_HASHES, new_hashes=current)])
        if paper:
            cli.atomic_json(runtime / experiment.FILE, updated)
        return dict(mode='paper', status='upgraded' if paper else 'eligible', written=paper,
                    telemetry_schema=experiment.TELEMETRY_SCHEMA)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', type=Path, default=cli.DEFAULT_RUNTIME)
    parser.add_argument('--paper', action='store_true', help='write the reviewed seal migration')
    args = parser.parse_args()
    try:
        import json
        print(json.dumps(upgrade(args.runtime, paper=args.paper), allow_nan=False))
        return 0
    except Exception as error:
        # No paths, raw documents, credentials or arbitrary exception text.
        print('Telemetry migration rejected (' + type(error).__name__ + '); state preserved.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
