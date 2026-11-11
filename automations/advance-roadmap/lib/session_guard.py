#!/usr/bin/env python3
"""Reap a bounded provider process group if its stream supervisor disappears."""
import argparse
import os
import signal
import time
from pathlib import Path

import workqueue


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--parent', type=int, required=True)
    p.add_argument('--child', type=int, required=True)
    p.add_argument('--child-start', required=True)
    p.add_argument('--timeout', type=float, required=True)
    p.add_argument('--ready', required=True)
    args = p.parse_args()
    Path(args.ready).write_text('ready\n')
    deadline = time.monotonic() + args.timeout if args.timeout > 0 else None
    while workqueue.process_start(args.child) == args.child_start:
        try:
            os.kill(args.parent, 0)
            parent_alive = True
        except OSError:
            parent_alive = False
        if not parent_alive or (deadline is not None and time.monotonic() >= deadline):
            try:
                os.killpg(args.child, signal.SIGTERM)
                time.sleep(5)
                if workqueue.process_start(args.child) == args.child_start:
                    os.killpg(args.child, signal.SIGKILL)
            except ProcessLookupError:
                pass
            break
        time.sleep(.5)


if __name__ == '__main__':
    main()
