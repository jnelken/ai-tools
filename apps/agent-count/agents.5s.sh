#!/bin/zsh
# <xbar.title>Active agents</xbar.title>
# <xbar.desc>Working Claude and Codex sessions on this Mac and Superset hosts.</xbar.desc>
# <swiftbar.runInBash>false</swiftbar.runInBash>
# <swiftbar.refreshOnOpen>true</swiftbar.refreshOnOpen>
export PATH="$HOME/.local/bin:$HOME/.local/share/mise/shims:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
exec /usr/bin/python3 "${0:A:h}/collector.py" "$@"
