# ADR 0001: Compose instructions by role and target repository

- **Status:** Accepted; implemented, with rollout checks remaining
- **Decision date:** 2026-10-19
- **Recorded:** 2026-10-21

## Context and intent

Slackagent uses the same local model CLIs as interactive development. Launching those CLIs from a source checkout can load repository instructions plus global development procedures, hooks, skills, plugins, and integrations. A household conversation therefore inherits context intended for coding, even when the application supplies a small conversational prompt. The concern is unnecessary context and conflicting behavior, not just the size of one AGENTS.md file.

The same configuration must support personal development and work development. On the personal Mac, `~/Dropbox/code` holds personal projects while `~/code` is used for work. A remote-control session often starts at a code root and moves between repositories. Neither that startup directory nor the machine alone reliably identifies the policy for every later target.

The intent is to give each task relevant guidance, preserve existing working agreements, and make selection inspectable across machines. Personal push permissions must stay scoped to personal repositories. Runtime assistants must acquire only their deliberately selected configuration.

## Decision

Maintain instruction fragments, recipes, the resolver, and harness adapters in ai-tools. Install entry points as symlinks to the maintained deploy clone at `~/.ai-tools`. Dotfiles continues to own development settings and existing procedure references.

Compose shared preferences with the selected role and environment, provider-specific guidance where applicable, and agent-specific runtime guidance. Support these explicit recipes:

- `development/auto`, `development/work`, `development/personal`, and `development/unknown`.
- `runtime/slackagent` for conversational assistance and explicit assistant connectors.
- `runtime/text` for classification, summaries, and extraction without tools.

`agent-profile resolve --target <directory> --harness <provider> --explain` reports the selected recipe, reasons, and sources. `agent-run --profile <recipe> -- <CLI and arguments>` composes before launching. CLI `--profile` takes precedence over `AGENT_PROFILE`; runtime selection is always explicit.

Automatic development selection checks explicit repository mappings, recognized Git ownership, then configured code roots when no origin is available. Unrecognized ownership remains unknown, even inside a personal root. Machine-local configuration supplies paths and connector references; secrets do not belong in instruction fragments.

Direct CLI and desktop starts retain short global bootstraps that tell the agent to resolve guidance. This path is model-mediated. The launcher resolves before model startup. Neither path relies on parent `code/AGENTS.md` inheritance.

### Always-active enforcement contracts

Instructions required by always-active enforcement must be available before optional profile resolution. The bootstrap may therefore contain the minimum contract for a hook that runs on every turn, including a safe default for trivial turns. It should still defer full policy meanings and situational procedures to their maintained fragments.

Claude's development Stop hook checks every final response, whereas the resolver is only required before substantive work. A first-turn `ping` can therefore reach the hook without the provider fragment having been loaded. The Claude bootstrap carries the status-line format, accepted glyphs, and the answer-only default; the provider fragment remains authoritative for meanings and priorities. A contract check compares the advertised glyphs with the provider table and actual hook acceptance. Codex's bootstrap does not acquire this Claude-specific rule.

Wrapped development launches can still load the global bootstrap; an already-composed profile prevents redundant resolution. Only explicit runtime launches suppress the bootstrap and development hooks. Do not interpret all `agent-run` launches as bypassing global instructions.

Before working in each repository, development sessions resolve that target and read its local and applicable nested instructions. Repeat when switching repositories within one remote-control session. Previously loaded guidance remains historical context; its permissions do not transfer to another target. Explicit development overrides are deliberate caller choices.

For runtimes, control automatic configuration loading as well as the added prompt. Preserve subscription authentication and built-in harness instructions. Exclude development instructions, hooks, skills, memory, and unselected integrations. Use provider-specific adapters and fail rather than silently reverting to full development configuration when isolation controls are unavailable. Keep owner-only connectors out of household/background calls.

Profiles select context and configuration; they are not an OS security boundary, and repository switching does not erase earlier conversation history.

## Alternatives considered

| Alternative | Reason not adopted |
|---|---|
| Keep all guidance global and ask assistants to ignore irrelevant sections | Leaves the context overhead and competing procedures loaded. |
| Put a shared AGENTS.md above all repositories | Codex's normal project discovery does not provide that middle layer above individual Git roots; it also does not express runtime intent. |
| Move source or runtime agents into separate directory trees | Could remove some checkout context, but global configuration still needs control. Relocation adds path/state migration without resolving the whole issue. |
| Give every development profile separate tools and hooks immediately | Deferred. First preserve existing development configuration while isolating unattended runtimes. |

## Implementation plan adopted

1. Inventory global instructions and record every section's destination. Preserve policy intent; keep detailed procedures and frontend guidance available on demand.
2. Add maintained fragments, a resolver, an explanatory interface, and a launcher with portable machine-local configuration.
3. Replace global instruction entry points with minimal bootstraps and support per-target repository switching. Retire only the installer-owned parent CLAUDE.md link.
4. Update ai-tools and dotfiles installation ownership. Preserve replaced files, verify symlinks, and make installation repeatable.
5. Route Slackagent model calls through explicit runtime recipes. Preserve model selection, cancellation, existing application-side finance/Linear behavior, and explicitly configured owner tools. Do not build missing integrations.
6. Verify fresh runtime sessions and profile selection, deploy through the maintained source workflow, and complete machine-specific rollout checks.

Source directories, working directories, databases, logs, caches, and reports stay in place. Existing development policies are reorganized rather than redesigned. Unrelated pending Slackagent work is excluded from the commits.

## Consequences, verification, and rollout

The design adds a small resolver and two harness adapters, but provides one maintained policy source and a way to explain selection. Direct starts remain dependent on agents following the bootstrap. Fresh sessions are required after profile changes; the migration does not rewrite existing prompts.

CLI behavior must be verified locally. During implementation, Claude safe mode suppressed even explicit MCP connectors, so connector-enabled calls required a separate restricted configuration. Claude's bare mode was unsuitable for the existing subscription login. Installed Codex configuration also differed from the current documentation. The adapters and compatibility checks record these constraints; operational details belong in the runbook rather than being frozen in this decision.

Resolver, installer, and runner tests cover scope selection, worktrees and symlinks, unknown repositories, argument handling, cancellation behavior, and failure without an available launcher. Fresh Claude/Codex runtime checks verified subscription authentication and runtime-only context; the owner Claude check exposed only the selected Mailcrush connector. Work-machine and code-root remote-control smoke checks still require their own rollout verification. Runtime cutover must be verified separately from installing shared tooling; this ADR does not assert the pending Slackagent restart occurred. No before/after latency benchmark was completed.

The migration also exposed installer friction. A follow-up makes self-updates restart once into the deployed installer, preserving arguments and skipping another update. Dirty-deploy diagnostics explain executable-bit drift and explicit preservation/repair steps without discarding edits. These are delivery safeguards supporting the decision.

Rollback restores preserved global entry points and the previous runner configuration, reverts the source changes so future installs do not reapply them, and starts fresh sessions. No runtime-state migration needs reversing.

## Evaluation criteria

Evaluate this decision on task outcomes and total operating effort, not merely on shorter instruction files. The thresholds below are review triggers, not measurements already established or permission to change shared tooling automatically.

| Criterion | Evidence to collect | Acceptable result / review trigger |
|---|---|---|
| Runtime relevance and isolation | Fresh-session instruction sources, available skills/tools, hook activity, and unexpected repository actions for both providers and runtime recipes | Only the intended runtime context and tools appear. One confirmed development-context leak, unintended connector, or unauthorized action requires stopping or rolling back the affected adapter until understood. |
| Work/personal scope | Resolver output and observed behavior at code roots, inside known and unknown repositories, and after switching targets in one session | Classification matches the target; unknown targets receive no personal grants. A cross-target permission leak requires immediate correction. Two independent sessions needing the same manual classification correction warrant revising mappings or selection rules. |
| Task capability and answer quality | Representative text tasks, household questions, owner connector tasks, and development tasks using required local guidance | Tasks retain required context and integrations without unrelated procedures. Missing context that causes wrong answers or repeated manual reattachment is a failure even if token use falls. |
| Startup and context cost | Compare instruction size, resolver/launcher overhead, and end-to-end latency on matched tasks; keep provider, model, CLI version, authentication state, and tool set fixed | Establish a baseline before claiming savings. Review the design if measured overhead, retries, or re-reading cancel the savings; do not attribute network/model variation to the resolver. |
| Operational effort | Record profile corrections, compatibility-check failures, installer retries, duplicated policy edits, and time spent repairing profiles across machines | The system should remove more repeated work than it creates. Repeated maintenance for the same failure in two independent sessions triggers a focused review rather than another per-instance workaround. |
| Portability and recovery | Personal- and work-machine smoke checks, deployed-source links, repeat installation, and a rehearsal restoring preserved entry points | No hardcoded machine paths or lost subscription access. Work-machine support stays unverified until tested there. Failure to reproduce or roll back means rollout is incomplete. |

Keep benchmark observations local and avoid recording raw private conversations or credentials. For timing comparisons, use several matched runs and retain both instruction-size and latency observations; a single fast request is not evidence of improvement. Use the existing test suites for deterministic behavior, and fresh sessions for actual harness loading behavior.

## Gotchas to watch

- **A bootstrap is guidance, not a startup hook.** Direct desktop/CLI starts depend on the model following it. A wrapper can compose before launch, but repository switching inside a conversation still requires target-specific resolution.
- **Explicit overrides can pin the wrong environment.** Use `development/auto` for sessions spanning repositories. `AGENT_PROFILE` is inherited by subprocesses; inspect `--explain` when selection is surprising. A correctly selected startup profile does not establish the policy for every later target.
- **Loaded context is not unloaded.** Resolving a new target does not erase earlier instructions, secrets, or conversation history. A session that needs a stronger work/personal privacy boundary should start separately rather than relying on policy scoping alone.
- **Names and source pointers are not proof of isolation.** A runtime label, a connector allowlist, or a list of composed files does not prove the harness excluded other sources. Check actual loaded tools, skills, and hook behavior. References printed by the resolver also need verification when their content is required; do not assume harness-native import syntax is expanded in tool output.
- **Allowlisted connectors can still write.** Mailcrush exposes mutation tools as well as reads. Selection does not authorize actions or enforce per-user access; the application and user request remain authoritative. Keep owner-only configuration out of globally inherited runtime connector settings.
- **Credentials can travel with connector configuration.** Keep secrets outside versioned fragments and avoid logging full launch arguments or connector definitions. A server-name allowlist is not validation of the server's implementation or credential handling.
- **Harness controls change.** Claude safe mode, restricted mode, instruction-discovery environment variables, and subscription authentication have different semantics. Codex's installed schema can differ from current documentation. Compatibility guards must fail visibly; repeat smoke checks after upgrades before updating the locally verified version.
- **A successful install is not application cutover.** Existing processes and sessions can retain old code or prompts. Restarting a service from a dirty checkout can also load unrelated work; verify what will change before restarting.
- **Delivery safeguards remain load-bearing.** Read-only deploy clones and dirty-tree checks protect local edits. Diagnose and preserve drift explicitly; do not disable those checks to make an install appear successful.

## When to revise or retire this design

Review after the work-machine rollout, after a relevant harness upgrade, and whenever a trigger in the evaluation table occurs. A review should name the failed task, the selected sources and tools, and the observed cost or error. Change the smallest responsible layer first: a mapping, a fragment, an adapter, or an entry point.

Simplify or replace the approach when any of these patterns persists:

- Profile selection and repeated context loading demand more attention than the irrelevant context they removed, with no demonstrated improvement in answer quality, startup, or instruction size.
- The same policy needs edits in several maintained copies, or recipe/provider combinations proliferate without distinct task needs. Consolidate the source or remove an unused variant.
- Agents repeatedly ignore bootstrap or repository-switching guidance. Use a more deterministic entry point or separate sessions; adding more prose is not an adequate substitute for reliable loading.
- Harness changes repeatedly break the adapter or require fragile private controls. Prefer a supported configuration surface or a different runtime integration over weakening isolation checks.
- Runtime assistants regularly need development capabilities. Revisit that task boundary explicitly instead of quietly restoring the full development configuration to all runtime calls.
- A repository or organization changes its ownership, directory layout, or authorization model. Re-evaluate classification and grants before inheriting the previous mapping.

Do not redesign solely because a guard blocked an unsafe or unverified launch: that may be the intended result. Distinguish a protection working correctly from a recurring compatibility burden. Record any changed decision in a follow-up ADR that supersedes this one, update the runbook and migration guidance, and preserve a rollback path.

## Implementation references

- [Operational runbook](../docs/instruction-profiles.md)
- [Instruction migration index](../instructions/MIGRATION.md)
- [Resolver and harness adapters](../lib/agent_profiles.py)
- [Profile installer](../bin/install-agent-profiles)
- Initial changes: ai-tools `0dd0601`, dotfiles `4a51232`, Slackagent `2385db6`.
- Installer hardening: ai-tools `45cc300`.
