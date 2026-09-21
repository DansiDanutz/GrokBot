#!/usr/bin/env python3
"""Load only named private credentials, then replace this process with its job."""
import argparse
import json
import os
from pathlib import Path
import stat
import sys


ALLOWED_KEYS = frozenset({'DLS_TELEGRAM_BOT_TOKEN', 'SEMECLAW_BOT_TOKEN',
                          'TYPESAFE_API_KEY'})


def credentials(path):
    path = Path(path)
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('unsafe credential path')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_size > 16384:
            raise ValueError('unsafe credential permissions')
        with os.fdopen(fd, encoding='utf-8') as stream:
            fd = None
            result = json.load(stream)
    finally:
        if fd is not None:
            os.close(fd)
    # A fixed allow-list, not arbitrary keys: the file decides which variables
    # reach the child process, so an attacker who can write it must not be able
    # to inject PATH or DYLD_*. Widened 2026-09-14 for the SemeClaw bridge,
    # which reads a differently-named token; one credential mechanism on this
    # machine beats a second bespoke loader.
    if not result or set(result) - ALLOWED_KEYS:
        raise ValueError('invalid credential configuration')
    if any(not isinstance(v, str) or not v for v in result.values()):
        raise ValueError('invalid credential configuration')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    # Repeatable so one job can load two unrelated services without their keys
    # sharing a file: --credentials telegram.json --credentials typesafe.json
    parser.add_argument('--credentials', required=True, action='append')
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    try:
        if not command or not Path(command[0]).is_absolute():
            raise ValueError('absolute executable required')
        env = dict(os.environ)
        loaded = {}
        for path in args.credentials:
            for name, value in credentials(path).items():
                if name in loaded:
                    raise ValueError('duplicate credential name')
                loaded[name] = value
        env.update(loaded)
        os.execvpe(command[0], command, env)
    except (OSError, ValueError, TypeError):
        print('Private credential loader failed; values omitted.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
