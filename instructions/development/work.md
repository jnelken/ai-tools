## Post-PR Workflow

`/post-pr` owns this end to end: review until clean → watch CI → then, by the PR's base branch, either merge it into its trunk (sub-PR) or post to `#pr-review` → badge the description → undraft (PR into `main`). The skill holds the mechanics (tokens, channel ID, dedup state, repo labels, the exact gates). Don't re-derive them here.

Bundle closely-knit work: land pieces as sub-PRs (merge commit, never squash) into one trunk `feature/<slug>` branch and announce only the trunk's PR into `main`; a standalone one-off fix goes straight to `main`. Details: `~/.claude/instructions/post-pr-trunk-flow.md` (trunk/sub-PR rules, which flow to pick, the gates).

**Run it automatically, and don't stop for my review.** Once a task's work is complete — the thing I asked for is finished, the tree is clean, the branch is pushed, and a PR exists — start `/post-pr` immediately. Don't offer it, don't ask whether to proceed, don't wait for me to confirm. If several PRs finished together, run it for each. For a sub-PR that means review, CI, the trunk merge, and reaping its sswt; the trunk PR's own run fires when I say the trunk is done, not on every sub-merge. This standing instruction is the authorization for the Slack post, the trunk merge and the sub-PR reap, so treat it as durable and skip the usual confirmation for an outward-facing action.

Do **not** fire it on intermediate WIP pushes mid-task, or on a branch whose PR is deliberately parked. "Work is complete" means the task is done, not that a push happened. Invoke `/post-pr` by hand for a later push to an already-shared PR.

Because it self-starts, it must self-report: say plainly what happened to each PR, and if a gate stopped the run, name the gate and what's outstanding rather than burying it.

The one place `/post-pr` asks: if a PR into `main` has a screenshot in its description, the run ends with a single question offering it to `#product-updates`. That post is user-facing and not covered by the standing authorization above.

When local Codex is throttled or out of credits, fall back to a GitHub-triggered review on the *other* credential pool: local on a ChatGPT login (`auth_mode: chatgpt` in `~/.codex/auth.json`) → `/codex-action-review` (API key); local on an API key → `@codex review` (ChatGPT plan). Either may be posted without asking; say in the report that you switched and why. `/babysit-pr` only addresses review threads that already exist.


## Superset Workspaces (sswt)

Read `~/.claude/instructions/superset-workspaces.md` before creating, renaming or reasoning about a workspace. Hard rules:

- **Never build a feature in a root checkout** (`/Users/jake/code/woodrow`, `/Users/jake/code/api`) — create a sswt first. Exception: a repo whose own `CLAUDE.md` says to work in its root checkout (folio-platform's batch-branch flow).
- **Never switch branches in a worktree.** In a sswt, new work stays on its branch; a new branch means a new sswt.
- **Use the local `superset` CLI**, not host-scoped `mcp__superset__*` tools — the relay never connects on this machine.
- **Default to jake2 for non-UI sswts.** jake1 freezes under memory pressure during parallel agent work, so create sswts with `--host` jake2 when it is online and the work doesn't impact UI; UI work stays on jake1. Mechanics in the doc above.
- **Title** is `#<pr> <CON-ID> <short-slug>`; add the PR number as soon as the PR opens.


## Datadog

Default to the `pup` CLI; reach for the `claude.ai Datadog` MCP connector only when `pup` lacks the surface. After a Datadog debugging session that hit observability gaps, close with a **Follow-up** note and file one `telemetry`-labeled Linear ticket. Details: `~/.claude/instructions/datadog.md`.

