#!/usr/bin/env bash
# No models; read-only subscription quota at session boundaries.
exec python3 "${AI_TOOLS_HOME:-$HOME/.ai-tools}/bin/claude-session" hook
