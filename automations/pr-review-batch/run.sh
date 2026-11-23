#!/bin/sh
# Stable entry point for hand runs and callers (post-pr, pr-review-gaps): `run.sh --dry-run`,
# `run.sh --only <url>`, `--channel <id>`. The plist runs the same thing through script-automation.
exec "$HOME/.ai-tools/bin/script-automation" pr-review-batch flush.py "$@"
