#!/usr/bin/env python3
"""Claim one reserved reviewer invocation for a bounded worker unit."""
import argparse
import fcntl
import json
from pathlib import Path


def claim(path, provider, allowed):
    if provider not in allowed:
        return False
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(path) + '.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            used = json.loads(path.read_text())
        except FileNotFoundError:
            used = {}
        except (OSError, ValueError):
            return False  # unknown prior spend must fail closed
        if used.get(provider, 0) >= 1:
            return False
        used[provider] = 1
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(used) + '\n')
        temporary.replace(path)
        return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('path')
    parser.add_argument('provider')
    parser.add_argument('allowed', nargs='*')
    args = parser.parse_args()
    return 0 if claim(args.path, args.provider, args.allowed) else 3


if __name__ == '__main__':
    raise SystemExit(main())
