#!/usr/bin/env python3
"""Load only named private credentials, then replace this process with its job."""
import argparse
import json
import os
from pathlib import Path
import stat
import sys


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
    if set(result) != {'DLS_TELEGRAM_BOT_TOKEN'} or not isinstance(result['DLS_TELEGRAM_BOT_TOKEN'], str) or not result['DLS_TELEGRAM_BOT_TOKEN']:
        raise ValueError('invalid credential configuration')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--credentials', required=True)
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    try:
        if not command or not Path(command[0]).is_absolute():
            raise ValueError('absolute executable required')
        env = dict(os.environ)
        env.update(credentials(args.credentials))
        os.execvpe(command[0], command, env)
    except (OSError, ValueError, TypeError):
        print('Private credential loader failed; values omitted.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
