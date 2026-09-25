---
name: linear-cli
description: Read and write Linear from the shell with the `linear` CLI — the fallback whenever the Linear MCP server is logged out, its token expired, or its tools aren't loaded (ToolSearch shows only authenticate/complete_authentication). Use for querying issues, reading a ticket with its comment threads, listing labels, and raw GraphQL. Covers the default filters that make `linear issue list` return "No issues found" for issues that exist. Sibling of [[linear-ticket-gen]], which owns the conventions for *authoring* a ticket; this skill owns the mechanics of talking to Linear.
---

# Linear from the CLI

`linear` is a full-featured Linear client that works when MCP doesn't. It authenticates from a
long-lived token in the environment rather than an OAuth session, so it keeps working through
exactly the failures that take MCP down.

**Reach for this the moment MCP is unavailable** — don't stop, don't ask Jake to re-auth, don't
fall back to raw `curl`. Verified working 2026-09-25 in a session where
`mcp__plugin_linear_linear__*` was returning `requires re-authorization (token expired)`.

## Setup facts

| | |
|---|---|
| Binary | `/opt/homebrew/bin/linear`, v2.5.0 |
| Auth | `LINEAR_API_TOKEN` in the environment (a `lin_api_…` key) |
| Workspace | `jnelken` — one team, **Dev**, key `DEV` |

**Check the token with `printenv \| grep -i LINEAR` rather than assuming a source.** It is *not*
`LINEAR_API_KEY` (unset on this machine), and it was not found in `~/.zshrc`, `~/.zshenv`,
`~/.zprofile`, or `~/.claude/settings*.json` — so don't tell the user where it comes from. If
it's missing, say so instead of guessing at a workaround.

Sanity-check auth and workspace in one call:

```bash
linear api '{ viewer { name } organization { name urlKey } }'
```

## The trap: empty results instead of errors

**This is the single most important thing in this skill.** These commands answer
`No issues found` — not an error, not a warning — when a default filter excluded everything. It
reads exactly like ground truth.

`linear issue list` is an **alias for `linear issue mine`**, and it is a personal inbox, not a
search. Three defaults stack:

1. **Assignee-scoped** — unassigned issues never appear, whatever else you pass.
2. **`--state` defaults to `["unstarted"]`** — Backlog issues are excluded unless you add
   `--all-states`.
3. Combining it with `--label` looks like a search and isn't.

Observed: `linear issue list --team DEV --all-states` returned six assigned `audio-setup`
tickets while `DEV-12`–`DEV-17` — unassigned, Backlog, labelled `repo/knowledge-vault` — were
invisible. Four different invocations answered `No issues found` before the cause was found.

**The rule that generalizes: when a read comes back empty and you have reason to believe rows
exist, re-run it through `issue query` or `linear api` before believing the empty result.**

## Reading — verified commands

Every command in this section was **executed** against the live workspace.

**Search — this is the one you want.** `issue query` defaults to *all* states and *all*
assignees, which is what "find me issues" should mean:

```bash
linear issue query --team DEV --label knowledge-vault      # by label
linear issue query --all-teams --search "pgvector"         # full-text
linear issue query --team DEV --unassigned --state backlog # narrow deliberately
linear issue query --team DEV --label foo --json           # parseable
```

Useful flags: `--search-comments` (needs `--search`), `--project`, `--cycle active`,
`--milestone`, `--assignee`, `--updated-after`, `--include-archived`, `--limit 0` for unlimited,
`--json`, `--no-pager`.

**Filter on the label's own name, not its group path.** For the `repo` label group, that means
`--label knowledge-vault`, never `--label repo/knowledge-vault`.

**One issue, with its comment threads** — includes thread ids, which is what you need to reply
to a specific thread:

```bash
linear issue view DEV-12
```

**Labels:**

```bash
linear label list --team DEV     # shows id, name, colour, and Workspace vs Team scope
```

**Raw GraphQL — the escape hatch**, for anything the flags can't express:

```bash
linear api 'query { issues(filter: { labels: { name: { eq: "knowledge-vault" } } }, first: 10) { nodes { identifier title state { name } } } }'
linear schema | head -100    # print the schema instead of guessing at filter shapes
```

Prefer `issue query` over `api` when a flag exists for what you need — it's shorter and its
output is already formatted. Drop to `api` for aggregate queries, unusual filters, and mutations
the write subcommands don't cover.

## Writing — inspected, not executed

**These were read from `--help` and never run.** Their flags are documented; their behaviour is
not verified. Confirm against `linear <cmd> --help` before trusting one against Jake's real
tracker, and prefer MCP for writes when it's available.

| Command | Purpose |
|---|---|
| `linear issue create` | Create an issue |
| `linear issue update <id>` | Update an issue |
| `linear issue comment` | Manage comments (replies, threads) |
| `linear issue start <id>` | Move to started |
| `linear issue link <url\|id> [url]` | Attach a URL |
| `linear issue attach <id> <path>` | Sidebar link attachment (images don't render inline) |
| `linear issue relation` | Dependencies / blocking |
| `linear issue pr <id>` | Open a GitHub PR carrying issue details |
| `linear issue id` / `describe` / `url` / `title` | Derive issue info from the current git branch |

Also present: `team`, `user`, `project`, `project-update`, `cycle`, `milestone`, `initiative`,
`initiative-update`, `document`, `config`, `completions`.

**`linear issue delete` is never run unprompted.** Same category as any destructive MCP call:
show what you intend to delete, get an explicit yes first.

## Conventions this repo's Linear depends on

- **Every issue carries a `repo/*` label**, single-select, one child per directory under
  `~/Dropbox/code`. It's how automation decides where to write code, so a wrong one means a push
  to the wrong repo. [[advance-roadmap]] treats a missing label as a blocker rather than
  inferring the repo, and the global `CLAUDE.md` requires applying one on every file/update.
- States in team Dev: `Backlog`, `Todo`, `In Progress`, `In Review`, `Done`, `Canceled`,
  `Duplicate`. The CLI's `--state` takes *types* (`triage`, `backlog`, `unstarted`, `started`,
  `completed`, `canceled`), not these display names.
- For what to put *in* a ticket — duplicate-checking first, evidence-grade bodies, label axes,
  the Triage trap — follow [[linear-ticket-gen]]. This skill only covers getting the bytes in
  and out.

## Common mistakes

- **Believing `No issues found`.** It is the symptom of a default filter, not evidence of an
  empty result set. Re-run through `issue query`.
- **Using `issue list` as a search.** It's `issue mine`. Unassigned work is invisible to it.
- **Passing `--label repo/knowledge-vault`.** Use the child name alone.
- **Reaching for `curl https://api.linear.app/graphql` by hand.** `linear api` already carries
  the auth and is shorter. (And `LINEAR_API_KEY`, which older notes reference, is unset here.)
- **Trusting a write command in this skill because it's written down.** The write table is
  `--help` output, not verified behaviour.
- **Asking Jake to re-authenticate MCP.** That's the situation this skill exists to route
  around. Use the CLI and mention in passing that MCP is stale.
