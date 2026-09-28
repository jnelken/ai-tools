# Archive

Retired pieces kept for reference. Nothing here is linked by `install.sh` or wired into `settings.json`.

| Path | Retired | Why |
|---|---|---|
| [`hooks/set-process-title.cjs`](hooks/set-process-title.cjs), [`hooks/set-process-name.sh`](hooks/set-process-name.sh), [`hooks/named-node-processes.md`](hooks/named-node-processes.md) | 2026-09-27 | The global `NODE_OPTIONS=--require …/set-process-title.cjs` got duplicated into child processes (the PreToolUse hook re-appended it), producing `--require X --require X` that Node parsed as one bad path — breaking `next build` workers. Process titling wasn't worth a global Node preload. Restore by moving the files back to `hooks/`, re-running `install.sh`, and re-adding the `env.NODE_OPTIONS` + PreToolUse entries per `named-node-processes.md`. |
