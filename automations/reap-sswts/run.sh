#!/bin/sh
# Stable entry point for hand runs: `run.sh --dry-run` previews what it would do. The plist runs the
# same thing through script-automation.
exec "$HOME/.ai-tools/bin/script-automation" reap-sswts reap.py "$@"
