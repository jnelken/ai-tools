---
name: release
description: "Release the /post-pr queue early: post queued PRs to #pr-review now (with the flusher's badge, undraft and auto-merge) instead of waiting for the next :00/:30 window, on this machine and the other Mac. Use for \"/release\", \"release the queue\", \"post the queued PRs now\", \"flush pr-review now\", \"don't wait for the window\". Pass PR URLs to release only those, or `all` to also skip sibling and 4 PM deferral holds."
allowed-tools: Bash(python3 -I ~/.claude/skills/release/scripts/release.py:*), Bash(superset:*)
---

# Release

`/post-pr` never posts directly. It queues a PR in `~/.claude/state/pr-review-queue.jsonl`, and the `pr-review-batch` flusher posts the queue at the next `:00`/`:30` window. After the post, the flusher badges, undrafts and turns on auto-merge (see [[post-pr]] steps 4–6). `/release` runs that same flusher now. It never posts, badges or undrafts by hand, so the invariant in post-pr still holds: only the flusher makes the post.

Running `/release` is the request to post. Don't ask again before posting.

## Arguments

```
/release              # whole queue now; keep the sibling hold and 4 PM deferral holds
/release all          # whole queue now, including held entries
/release <PR_URL>...  # only these PRs, holds skipped
/release list         # show both queues, post nothing
```

- **No argument** runs `run.sh --force`. It skips the window but keeps two holds: a cross-repo sibling that isn't queued yet (up to `siblingHoldMin`, default 60 minutes), and a deferred entry's `releaseAt` (the 4 PM bundle). Held entries stay queued and the report lists them.
- **`all` or URLs** run `run.sh --only <url>` per entry. That skips both holds, but a deferred bundle still needs green checks. A PR released alone ahead of its sibling posts without the sibling pairing line.

## Steps

1. **This machine.** Run
   ```
   python3 -I ~/.claude/skills/release/scripts/release.py [--all | --list | URL ...]
   ```
   Pass `all` as `--all` and `list` as `--list`. The script prints the queue, runs the flusher, then prints the `flush.log` lines this run wrote and what's still queued. Exit code 1 means a requested URL wasn't posted; the log line says why (dropped, held, requeued).
2. **The other Mac.** Queues are per machine: jake1 and jake2 each run their own `/post-pr` and flusher. This machine is jake2 when `$USER` is `jake2`, otherwise jake1. Get the other host's id from `superset hosts list --json`. If it is offline, report `other Mac offline, its queue untouched` and skip this step. Otherwise run the same command there through a throwaway terminal in any of its workspaces:
   ```
   WS=$(superset workspaces list --host <other-id> --json | jq -r '.[0].id')
   T=$(superset terminals create --host <other-id> --workspace "$WS" --json \
         --command "python3 -I ~/.claude/skills/release/scripts/release.py <same args>; echo RELEASE_EXIT=\$?" | jq -r .terminalId)
   superset terminals wait --host <other-id> --workspace "$WS" --terminal "$T" --match 'RELEASE_EXIT=\d' --timeout 300
   superset terminals read --host <other-id> --workspace "$WS" --terminal "$T"
   superset terminals close --host <other-id> --workspace "$WS" --terminal "$T"
   ```
   URL arguments go to both machines. Each one ignores URLs that aren't in its own queue.
3. **Report.** For each machine, list what posted (`posted <repo>#<n>: badge=… draft=… automerge=…`), what's still held and why, and anything dropped or not posted. If both queues were empty, say so in one line.

Never point the flusher at a real channel to test this skill. `--channel <id>` on `run.sh`, or the env var `PR_REVIEW_BATCH_CHANNEL`, sends posts to Jake's own DM instead.
