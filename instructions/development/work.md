## Post-PR Workflow

`/post-pr` owns this end to end (review until clean, CI, then queue for the next `#pr-review` window, or merge a sub-PR into its trunk) and holds the mechanics: tokens, channel ID, dedup state, the exact gates, the Codex fallback. Don't re-derive them here. Trunk/sub-PR flow, when `/create-pr` stacks a new PR automatically, and which flow to pick: `~/.claude/instructions/post-pr-trunk-flow.md`. A queued PR stays draft until it is posted.

**Run it automatically, and don't stop for my review.** Once a task's work is complete — the thing I asked for is finished, the tree is clean, the branch is pushed, and a PR exists — start `/post-pr` immediately. Don't offer it, don't ask whether to proceed, don't wait for me to confirm. If several PRs finished together, run it for each. For a sub-PR that means review, CI, the trunk merge, and reaping its sswt; the trunk PR's own run fires when I say the trunk is done, not on every sub-merge. This standing instruction is the authorization for the Slack post, the trunk merge and the sub-PR reap, so treat it as durable and skip the usual confirmation for an outward-facing action.

Do **not** fire it on intermediate WIP pushes mid-task, or on a branch whose PR is deliberately parked. "Work is complete" means the task is done, not that a push happened. Invoke `/post-pr` by hand for a later push to an already-shared PR.

**A finished PR that is waiting on approval always gets `/post-pr`. Never raise it as a decision.** When a PR into `main` is clean and blocked on `REVIEW_REQUIRED`, run `/post-pr` without asking how to get it approved, and merge it once an approving review exists. Jake chose this as the default on 2026-11-19 (api #1764, decision G): the standing instruction above is the authorization for the merge-after-approval, and enabling auto-merge to do it needs no extra question. Still ask for the product-update post below.

**Auto-merge is the preferred way to merge, with two conditions.** Enable it (`gh pr merge --auto`, squash unless the repo disallows it) when (1) the PR does not depend on another PR merging first, and (2) there is no open question about the implementation or about merge order with other work. If either fails (a PR stacked on or waiting for another, a cross-repo deploy order to respect, an unresolved design point), leave auto-merge off, say which condition failed, and merge by hand once it is clear. A PR that others depend on (an api PR a woodrow PR consumes) still qualifies; only the PR's own prerequisites count.

Because it self-starts, it must self-report: say plainly what happened to each PR, and if a gate stopped the run, name the gate and what's outstanding rather than burying it.

The one place `/post-pr` asks: if a PR into `main` has a screenshot in its description, the run ends with a single question offering it to `#product-updates`. That post is user-facing and not covered by the standing authorization above.

## Do not ask (decisions audit, 2026-11-23)

From Jake's replies to Decisions blocks, 2026-10-29 to 2026-11-20 (about 1,170 decisions). Post-PR and auto-merge are already covered above; the two below are the rest. Do them and report in the Verdict. Everything else stays a decision.

**Reap finished local work without asking.** When a PR is merged, or its content has landed on main, and the worktree is clean (no stashes, no unpushed commits) with no live session attached, delete the Superset workspace (`mcp__superset__workspaces_delete`, with `hostId` for another host), the worktree and the local branch. A workspace Claude dispatched counts, as does a worktree Claude made on this machine. Jake chose this on 2026-11-20 ("always delete the workspace if the work is fully merged, don't ask"). "Fully merged" means the PR merged and no follow-up is queued for that branch. Still ask for: secrets, keys or `.env` files; stage, prod or demo data; third-party content (Supademo, Netlify); SaaS objects (Linear, Slack, Neon, agents, vaults); remote branches or PRs; a dirty or stashed tree; killing a process or session. Never delete by glob or in bulk: list the exact targets first, and delete only those. "No live session" covers every host, not just this one. Never infer "idle" from age (Jake cycles through about 15 sessions, so an idle-looking tab may be live). This is separate from `reap`, which closes the session itself.

**Open a draft PR once local review is clean.** Push the branch and open it as a draft without asking. Still ask when the branch is on hold or being superseded, it stacks on an unmerged parent, Jake's last message was a question, a sibling decision is open, or the next step would undraft, merge or deploy it.

Not automated, whatever the accept rate: spec or design choices, QA ownership, scope, flags and env, deploys, ticket filing, agent spawning, peer-review fallback, and anything touching secrets, vault recipients or prod data. The 20-minute push rule to `main` stays a question, since Jake has overridden it by hand.


## Secrets and `.env` files

Env-var secrets come from local, git-ignored `.env` files (the 1Password vault `ENV` is the source of truth). Never print values. **On an agent host (jake2, any Mac driven remotely), never run `op`**: it needs Touch ID at the keyboard and only times out; ask Jake to run `zsh-secrets scratch pull <NAME>` instead. Read `~/.ai-tools/instructions/reference/secrets-env.md` before pulling, rotating, moving or writing any secret.


## Datadog

Default to the `pup` CLI; reach for the `claude.ai Datadog` MCP connector only when `pup` lacks the surface. After a Datadog debugging session that hit observability gaps, close with a **Follow-up** note and file one `telemetry`-labeled Linear ticket. Details: `~/.claude/instructions/datadog.md`.

