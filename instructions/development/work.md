## Post-PR Workflow

`/post-pr` owns this end to end: review until clean → watch CI → then, by the PR's base branch, either merge it into its trunk (sub-PR) or queue it for the next 30-min `#pr-review` window (PR into `main`); the `pr-review-batch` flusher then posts it, badges the description, undrafts it and enables auto-merge. A queued PR stays draft until it is posted. The skill holds the mechanics (tokens, channel ID, dedup state, repo labels, the exact gates). Don't re-derive them here.

Bundle closely-knit work: land pieces as sub-PRs (merge commit, never squash) into one trunk `feature/<slug>` branch and announce only the trunk's PR into `main`; a standalone one-off fix goes straight to `main`. One case stacks automatically: when `/create-pr` opens a new PR in woodrow, api or folio-platform and the same `CON-N` (or failing that, another ticket in the same Linear project, minus a denylist of umbrella projects) already has an open PR into `main` that isn't approved, the new PR becomes a sub-PR of that PR's branch, without asking. An approved PR is never stacked onto. Details: `~/.claude/instructions/post-pr-trunk-flow.md` (trunk/sub-PR rules, which flow to pick, the gates).

**Run it automatically, and don't stop for my review.** Once a task's work is complete — the thing I asked for is finished, the tree is clean, the branch is pushed, and a PR exists — start `/post-pr` immediately. Don't offer it, don't ask whether to proceed, don't wait for me to confirm. If several PRs finished together, run it for each. For a sub-PR that means review, CI, the trunk merge, and reaping its sswt; the trunk PR's own run fires when I say the trunk is done, not on every sub-merge. This standing instruction is the authorization for the Slack post, the trunk merge and the sub-PR reap, so treat it as durable and skip the usual confirmation for an outward-facing action.

Do **not** fire it on intermediate WIP pushes mid-task, or on a branch whose PR is deliberately parked. "Work is complete" means the task is done, not that a push happened. Invoke `/post-pr` by hand for a later push to an already-shared PR.

**A finished PR that is waiting on approval always gets `/post-pr`. Never raise it as a decision.** When a PR into `main` is clean and blocked on `REVIEW_REQUIRED`, run `/post-pr` (queue it for the next 30-min `#pr-review` window; the flusher posts, badges, undrafts and enables auto-merge) without asking how to get it approved, and merge it once an approving review exists. Jake chose this as the default on 2026-11-19 (api #1764, decision G): the standing instruction above is the authorization for the merge-after-approval, and enabling auto-merge to do it needs no extra question. `/post-pr` no longer asks about auto-merge: it records the method in the queue entry (or none, when a condition below fails) and the flusher enables it after the post. Still ask for the product-update post below.

**Auto-merge is the preferred way to merge, with two conditions.** Enable it (`gh pr merge --auto`, squash unless the repo disallows it) when (1) the PR does not depend on another PR merging first, and (2) there is no open question about the implementation or about merge order with other work. If either fails (a PR stacked on or waiting for another, a cross-repo deploy order to respect, an unresolved design point), leave auto-merge off, say which condition failed, and merge by hand once it is clear. A PR that others depend on (an api PR a woodrow PR consumes) still qualifies; only the PR's own prerequisites count.

Because it self-starts, it must self-report: say plainly what happened to each PR, and if a gate stopped the run, name the gate and what's outstanding rather than burying it.

The one place `/post-pr` asks: if a PR into `main` has a screenshot in its description, the run ends with a single question offering it to `#product-updates`. That post is user-facing and not covered by the standing authorization above.

When local Codex is throttled or out of credits, fall back to a GitHub-triggered review on the *other* credential pool: local on a ChatGPT login (`auth_mode: chatgpt` in `~/.codex/auth.json`) → `/codex-action-review` (API key); local on an API key → `@codex review` (ChatGPT plan). Either may be posted without asking; say in the report that you switched and why. `/babysit-pr` only addresses review threads that already exist.


## Secrets and `.env` files

The 1Password vault `ENV` (concentro.1password.com) is the source of truth for env-var secrets. Each variable is its own API Credential item, titled with the exact variable name, with the value in its `credential` field. Each item is tagged `woodrow`, `api`, both, or `scripts` (run ad hoc, not needed for the app to run). Page logins stay in `Engineering`.

- **Work from local, git-ignored `.env` files, not 1Password.** Put each value in the `.env` of the repo that reads it. Company-wide scripts and automations use `internal-tools/.env`, or `folio-platform/.env` when the tool is platform or observability tooling (Datadog, monitors, deploys), whichever fits.
- **New or rotated value, on a Mac Jake is sitting at:** pull it from `ENV` in one batched `op` run and write it to the matching `.env`. Each batch needs Jake to approve 1Password on that machine, and the approval lapses after about 10 idle minutes. Never print values.
- **On an agent host (jake2, any Mac driven remotely), never run `op`.** The approval needs Touch ID at the keyboard and an unlocked screen, so the call only times out, and asking Jake to approve it there cannot work. `dotfiles-role` says whether a Mac is an agent host; a `bin/op` shim on `PATH` blocks the call there and prints these steps. Instead:
  1. Jake, on the Mac he is at, one command for any number of names: `zsh-secrets scratch pull <NAME> [<NAME>...]`. It runs the batched `op read` from `ENV`, stores each value in scratch (`--ttl` to change the 7d default) and prints the agent-side command. Claude cannot trigger the Touch ID prompt, so Jake runs this himself; the value never goes on a command line.
  2. The agent, on the host, once `zsh-secrets scratch ls` shows `<NAME>`: `zsh-secrets scratch put <repo>/.env <NAME>`, or `zsh-secrets scratch exec --only <NAME> -- <cmd>` to inject it into one command without a file.
  If the value is not in scratch yet (`put` and `exec` say so and print the `pull` command), stop and ask Jake to run step 1 with the exact names. Do not retry `op` or loop on a timeout.
- **Between Macs:** move a value with `zsh-secrets scratch` (`pull NAME` from 1Password, or `set NAME` for a value that is not in `ENV`, on one Mac; `put <repo>/.env NAME` on the other). Shell-wide keys live in `~/.zsh_secrets`, synced by `zsh-secrets push`/`pull`.


## Datadog

Default to the `pup` CLI; reach for the `claude.ai Datadog` MCP connector only when `pup` lacks the surface. After a Datadog debugging session that hit observability gaps, close with a **Follow-up** note and file one `telemetry`-labeled Linear ticket. Details: `~/.claude/instructions/datadog.md`.

