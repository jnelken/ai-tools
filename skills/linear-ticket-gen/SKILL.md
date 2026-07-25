---
name: linear-ticket-gen
description: Use when creating or updating Linear issues — filing tickets from feedback or bug reports, checking for duplicate/overlapping tickets first, or resolving a Linear project/milestone/cycle URL when Linear MCP tools aren't authenticated (ToolSearch shows only authenticate/complete_authentication, no save_issue/list_teams).
---

# Linear Ticket Gen

## Overview

Turning feedback or a feature list into well-placed Linear issues: resolve the target team/project/milestone/cycle, check for overlap before creating duplicates, then create or update via the API. Org-specific facts (known project IDs, sizing conventions) belong in reference memory, not here — this skill is the procedure, memory is the data.

## When to use

- Asked to create, file, or triage Linear tickets from feedback, a bug list, or a feature list
- Given a Linear project/milestone URL that needs resolving to IDs
- `ToolSearch` for Linear only turns up `authenticate`/`complete_authentication` — no real issue tools — so the API-key fallback below is needed

## Access

Prefer MCP (`mcp__claude_ai_Linear__*` or `mcp__plugin_linear_linear__*`) if real issue tools are loaded. Otherwise:

```bash
printenv | grep -i linear   # check first — don't assume
curl -sS -X POST https://api.linear.app/graphql \
  -H "Authorization: $LINEAR_API_KEY" -H "Content-Type: application/json" \
  -d '{"query": "...", "variables": {...}}'
```

`LINEAR_API_KEY` lives in the user's personal zsh shell profile, not a project or CI env var — it's there in an interactive shell for that user, but don't assume it's set in a non-interactive script, CI, or another user's shell. If it's missing, say so rather than guessing at a workaround.

No `Bearer` prefix — Linear takes the raw key. Sanity-check the key/workspace once per session with `{ viewer{name} organization{name urlKey} }`.

## Resolve IDs before creating anything

| Need | Query | Gotcha |
|---|---|---|
| Project from a URL | `projects(filter:{slugId:{eq:"<last URL segment>"}})` | Pasted URLs can be stale — see below. Empty ≠ typo, could be a real mismatch. |
| Project's milestones | `project.projectMilestones` | Field is `projectMilestones`; `milestones` doesn't validate. |
| Team's cycles | `team.cycles` | No current/next flag — compute it: current cycle has `startsAt <= now <= endsAt`; next is the following one by `startsAt`. |
| User by email | `users(filter:{email:{eq:"..."}})` | — |
| Exact mutation fields | `__type(name:"IssueCreateInput"){inputFields{name}}` | Introspect instead of guessing, especially for less-common fields like `projectMilestoneId`/`cycleId`. |

### Stale URLs: don't silently pick the closest name

If a slugId doesn't resolve, **that is a blocker, not a typo to route around**. Search broadly (`projects(first:100)`, then `includeArchived:true`) to see what actually exists, but do not unilaterally pick whichever candidate sounds closest to the name you were given — a same-named active project and an archived one are not interchangeable, and guessing wrong means filing real tickets in the wrong place. Stop and ask the user which one they meant. A rename keeps the same `slugId`, so if nothing matches at all, the project may be deleted or the link may simply be wrong — say that plainly instead of proceeding on an assumption.

## Check for overlap before creating

Two passes, not one:

1. **Scoped** — list issues already in the target project/milestone and read titles *and* descriptions/state, not just titles.
2. **Workspace-wide** — `searchIssues(term:"<key phrase>")` for the core terms in the new ask. Related work often lives outside the target project/milestone.

If an existing issue already covers the ask, **update** it instead of duplicating. If the new ask contradicts something the ticket already documents (a prior decision, a different scope), don't silently overwrite — call out the conflict explicitly in the updated description and flag it to the user.

## Creating / updating

```graphql
mutation($input: IssueCreateInput!) { issueCreate(input: $input) { success issue { identifier url } } }
mutation($id: String!, $input: IssueUpdateInput!) { issueUpdate(id: $id, input: $input) { success issue { identifier url } } }
```

**Default for newly-created tickets, unless the user says otherwise:** `stateId` = the team's `Groomed` state, `cycleId` = the next upcoming cycle (compute per the cycle math above). Set both explicitly on every `issueCreate` — don't leave them unset and let Linear pick a default for you.

**For updates to existing tickets** (e.g. the overlap-check case above), the rule flips: only touch `assigneeId`/`cycleId`/`projectMilestoneId`/`stateId` if the user actually asked for that — don't pull an existing ticket into a cycle or change its state just because you edited its description.

### Why this matters: the Triage trap

If the team has `triageEnabled: true` (`team(id:"...") { triageEnabled }`), a new issue created **without an explicit `stateId` and without a `cycleId`** silently lands in the `Triage` state (`type: "triage"`, distinct from `backlog`) — hidden by default in the project Issues view (the "Show triage issues" toggle defaults off). It looks like the ticket didn't get created. This is exactly why state and cycle are set explicitly on every create above rather than left to Linear's default.

## Org conventions

Known project IDs, team ID, and sizing rules (milestone = 1–2 cycle chunk of work within a project; project = an ongoing feature area) live in reference memory — check there first rather than re-deriving from scratch, and update it when you learn something new or find a stale value.

## Common mistakes

- Querying `milestones` instead of `projectMilestones` on `Project`
- Creating a ticket without checking for overlap both in-scope and workspace-wide
- Trusting a pasted URL's slugId without verifying it resolves, or guessing between candidates when it doesn't
- Setting cycle/milestone/assignee on tickets the user didn't ask to schedule
