#!/bin/sh
# Runs the invariant template fixtures (Node only, no dependencies).
set -eu
cd "$(dirname "$0")/example"
exec node --test test/*.test.mjs
