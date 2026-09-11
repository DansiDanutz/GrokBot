"""Shared, public-safe summaries of explicitly recorded decision telemetry."""
from collections import Counter
from paper_grid.telemetry_constants import READABLE_BUY_REJECTION_REASONS


def rejections(events, observations, arm):
    observed = [o for o in observations if not o.get('skipped')]
    instrumented = [o for o in observed if o.get('telemetry_schema') == 1]
    groups = Counter()
    for event in events:
        if event.get('type') != 'buy_rejected' or event.get('account') != arm:
            continue
        reason = event.get('reason')
        reason = reason if isinstance(reason, str) and reason in READABLE_BUY_REJECTION_REASONS else 'unknown'
        action = event.get('action') if event.get('action') in ('open', 'add') else 'unknown'
        stage = event.get('stage') if event.get('stage') in ('execution', 'selection', 'rotation_trial') else 'unknown'
        groups[(reason, action, stage)] += 1
    coverage = 'complete' if instrumented and len(instrumented) == len(observed) else 'partial' if instrumented else 'unavailable'
    return dict(recorded=sum(groups.values()), coverage=coverage,
        instrumented_checks=len(instrumented), legacy_checks=len(observed)-len(instrumented),
        first_instrumented_at=min((o['time'] for o in instrumented), default=None),
        reasons=[dict(reason=r, action=a, stage=s, count=n) for (r,a,s),n in sorted(groups.items())])
