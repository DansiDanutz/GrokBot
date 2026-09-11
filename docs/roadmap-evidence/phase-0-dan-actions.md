# Phase 0: Dan-only actions (task 0.6)

Status: **proposals only; none of these commands were executed by Codex**.
These are outside the code change. Existing v1 and publisher services stay running.
Token rotation is optional here; the existing token was retained at Dan's request.
Do not run retirement commands until Dan confirms the older ladder is redundant.

## Retire only the older ladder job

First inspect the single label's presence without printing its configuration:

```sh
launchctl list | awk '$3 == "com.zmarty.ladder-paper-trader" { print }'
```

If Dan chooses retirement, run these exact commands in his own macOS login:

```sh
launchctl bootout "gui/$(id -u)/com.zmarty.ladder-paper-trader"
launchctl disable "gui/$(id -u)/com.zmarty.ladder-paper-trader"
install -d -m 700 "$HOME/.config/danslab/retired-launchagents"
mv -n "$HOME/Library/LaunchAgents/com.zmarty.ladder-paper-trader.plist" "$HOME/.config/danslab/retired-launchagents/com.zmarty.ladder-paper-trader.plist"
launchctl list | awk '$3 == "com.zmarty.ladder-paper-trader" { print }'
```

The last command should produce no row. If `bootout` fails for a reason other
than the job already being absent, investigate before continuing. `mv -n` never
overwrites a previous retirement copy. This does not delete account evidence or
stop `com.danslab.zmarty-paper48` / `com.danslab.trader-publisher`.

To undo this specific retirement, after confirming no conflicting running job:

```sh
mv -n "$HOME/.config/danslab/retired-launchagents/com.zmarty.ladder-paper-trader.plist" "$HOME/Library/LaunchAgents/com.zmarty.ladder-paper-trader.plist"
launchctl enable "gui/$(id -u)/com.zmarty.ladder-paper-trader"
launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.zmarty.ladder-paper-trader.plist"
```

## Optional C1 token rotation

A recommendation is not a claim that exposure occurred. If Dan later chooses
rotation, first identify every consumer of that exact bot and arrange a window;
revoking a shared token invalidates all consumers immediately. In Telegram,
Dan opens the verified **@BotFather**, sends `/revoke`, selects the exact bot,
and obtains the replacement privately. Do not paste it into chat, a shell
command, Git, a plist, or screenshots.

The existing credential wrapper reads
`~/.config/danslab/credentials/telegram.json` at each scheduled invocation. Dan
can replace that one existing private value without printing it or embedding it
in a command. The following operator-only command rejects unsafe permissions,
symlink paths and unexpected file shapes, and writes atomically:

```sh
python3 - <<'PY'
import getpass
import json
import os
from pathlib import Path
import stat
import tempfile

path = Path.home() / '.config/danslab/credentials/telegram.json'
key = 'DLS_TELEGRAM_BOT_TOKEN'
assert not any(p.is_symlink() for p in (path, *path.parents)), 'symlink path refused'
assert stat.S_ISREG(path.stat().st_mode), 'regular credential file required'
assert path.stat().st_uid == os.getuid(), 'wrong file owner'
assert path.stat().st_mode & 0o077 == 0, 'credential file must be private'
assert path.parent.stat().st_mode & 0o077 == 0, 'credential directory must be private'
document = json.loads(path.read_text())
assert isinstance(document, dict) and set(document) == {key}, 'unexpected credential shape'
replacement = getpass.getpass('Replacement bot token (hidden): ')
assert replacement and not any(c.isspace() for c in replacement), 'invalid replacement'
fd, temporary = tempfile.mkstemp(prefix='.rotation-', dir=path.parent)
try:
    with os.fdopen(fd, 'w') as handle:
        os.fchmod(handle.fileno(), 0o600)
        json.dump({key: replacement}, handle)
        handle.write('\n')
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
finally:
    if os.path.exists(temporary):
        os.unlink(temporary)
print('Private credential updated; no value displayed.')
PY
```

Dan must separately update any other confirmed consumer's private credential
source. This command deliberately does not restart jobs, send a Telegram message,
or create another poller. Next scheduled wrapper invocations read the replacement;
a long-running consumer needs a separately coordinated restart. Validate delivery
through an expected scheduled message in the intended private chat. Never use
`curl` with the token in the URL or command line for verification.

No migration to another secret store or plist rewrite is part of this proposal.
Historical protected backups can remain until Dan confirms rollback is no longer
needed; a revoked token is no longer usable.

_Last verified: 2026-09-11_
