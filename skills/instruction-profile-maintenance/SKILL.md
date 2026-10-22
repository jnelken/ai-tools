---
name: instruction-profile-maintenance
description: Maintain ai-tools instruction recipes, global bootstraps, profile selection, and harness loading or isolation. Use when changing how work, personal development, or runtime agents receive guidance; not for unrelated project-local AGENTS.md edits or general skill authoring.
---

# Instruction profile maintenance

Change the smallest layer that owns the requested behavior while preserving the profile boundaries. This skill is the maintenance workflow; the ADR owns architectural intent and the runbook owns operating commands.

## Establish the contract

Read the **Decision** in [ADR 0001](../../adr/0001-compose-instructions-by-role-and-repository.md) and the relevant sections of the [runbook](../../docs/instruction-profiles.md). When moving an existing policy, consult the [migration index](../../instructions/MIGRATION.md). Resolve these links against the real skill directory, not a symlink's parent.

State which task, target repositories, harnesses, and entry points must change. Inspect `agent-profile resolve --target <directory> --harness <provider> --explain` and the affected source to distinguish wrong selection, missing guidance, and guidance that was selected but never loaded. Avoid adding instructions to compensate for a loading defect.

Edit the development checkout. Installed links point into the read-only deploy clone at `${AI_TOOLS_HOME:-$HOME/.ai-tools}`; neither that clone nor an installed symlink is an editing destination. Preserve unrelated local edits.

## Choose the owner

| Requested behavior | Maintained owner |
|---|---|
| Preferences that apply across roles | `instructions/shared.md` |
| Coding agreements or work/personal grants | `instructions/development/` |
| Harness-specific development procedures | `instructions/providers/` |
| Assistant behavior or tool-free processing | `instructions/runtime/` |
| Discovery and minimum always-active hook contract | `instructions/bootstrap/` |
| Selection, composition, or CLI isolation controls | `lib/agent_profiles.py` and its launcher entry points |
| Installation ownership and maintained links | `bin/install-agent-profiles` or `install.sh` |

Keep meanings and detailed procedures in their existing authoritative fragment. A bootstrap may need a minimal rule and safe default when a hook runs even on turns that skip resolution; do not duplicate the full policy table. Check its contract against the actual hook. Wrapped development sessions may still load the bootstrap; explicit runtimes suppress it and development customizations.

For code-root sessions, evaluate the target repository again when it changes. Startup cwd is not the policy for every later target; explicit overrides can intentionally pin selection. Do not transfer personal grants to work or unknown targets, or introduce dependence on parent `code/AGENTS.md` inheritance.

Runtime changes must control inherited configuration as well as the added prompt. Preserve authentication, owner/household separation, and the existing working directory/state unless relocation is explicitly in scope. Allowlisting a connector does not make its tools read-only or authorize its actions. Keep credentials out of fragments, diagnostic output, and committed fixtures. A compatibility failure must not silently fall back to full development configuration.

## Verify the affected paths

Select checks by the behavior changed; do not make external calls for a prose edit that deterministic checks cover. Run relevant suites from the ai-tools development checkout with Python 3.11+ (the tests use stdlib `tomllib`):

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s lib -p 'test_agent_profiles.py'
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s lib -p 'test_installer.py'
bash hooks/tests/status-glyph-check.test.sh
python3 bin/install-agent-profiles --dry-run
```

- **Selection/composition:** check work, personal, and unknown targets; for ownership/path changes include worktrees, symlinks, and switching repositories from one code-root session. Inspect both providers' resulting sources for accidental scope expansion.
- **Bootstrap/hook:** check a fresh trivial first turn and a composed development session. Keep user instruction sources enabled during the first-turn probe; an empty `--setting-sources` can hide the bootstrap. Disable hook retries for that probe, then pass its first response to the real hook. Verify that unaffected providers and runtimes do not acquire the rule.
- **Harness/tool isolation:** inspect the installed CLI's capabilities and run fresh, non-mutating probes for affected recipes. Check actual tools, skills, hooks, and required guidance; a recipe label or unit-test pass is not proof of harness behavior. For a version-guard update, verify behavior before accepting the new version. Do not send messages or read private data merely to test tool exposure.
- **Installer:** use temporary local Git remotes and an isolated test home. Check preserved edits, maintained-source symlinks, repeat installation, argument handling, and update completion. Do not test repairs by dirtying the real deploy clone.

When a fresh probe or integration check fails, identify whether the test configuration excluded the guidance or capability under test before changing the recipe. Report untested entry points or machines as pending. Benchmark claims require a matched baseline; shorter text alone does not establish better productivity.

## Document and deliver

Update the runbook for changed operating behavior and the migration index when relocating policy. If architectural intent or boundaries change, use the ADR's **Evaluation criteria**, **Gotchas**, and **When to revise or retire** sections to decide whether a follow-up ADR should supersede the decision. Do not turn each small policy edit into a new architecture decision.

Use the repository's commit/push policy for a focused change, then run the normal deploy installer. Do not assume a local commit triggered redeployment. Verify the deployed revision and installed links, preserving links to the maintained deploy source. Check the actual application/process version separately before claiming runtime cutover; a restart from a dirty checkout can load unrelated work.

Finish with the selected layer, resulting behavior, verification and remaining limits. If the same loading or maintenance failure recurs, revisit the responsible mechanism using the ADR's revision triggers instead of accumulating more bootstrap prose.
