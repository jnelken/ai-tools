# Developer Workflow Kit

Public-source edition of the existing Unblock Roadmap plugin. The display name changes;
the package name `unblock-roadmap` and existing account plugin identity stay the same.
The original personal skills at `skills/<name>/SKILL.md` remain unchanged.

## Build

```sh
python3 bin/build-developer-workflow-kit /tmp/developer-workflow-kit.zip
```

The builder packages `skills/<name>/public/SKILL.md` as independent public skills,
generates the compatibility manifest, and includes referenced artwork. No runtime
GitHub instruction fetching, account bindings, or external server is required. This
source is a skills-only plugin; most workflows require actual checkout and shell access.
The account backend regenerates the compatibility manifest from the portable manifest,
including its review/publication fields. Compare parsed metadata on read-back rather
than assuming its key order or serialized text will match the uploaded compatibility file.

| Workflow | Source and public changes |
| --- | --- |
| Unblock roadmap | One decision at a time; persists in the user's chosen plan instead of requiring personal Linear labels |
| Advance roadmap | One bounded item in the selected repository; no personal scheduler, executor routing, or standing merge/deploy grants |
| Move diff | Local Git worktrees; preserves staging with `--index`, pins recovery refs, accounts for partially failed applies |
| Rebase after squash | Verifies the old parent tip; avoids treating individual patch-ID matching as proof of a multi-commit squash |
| Fix stale docs | Verifies references and history; no blanket permission to push into someone else's approved branch |
| Close out | Session-scoped cleanup and durable handoff; no mandatory issue tracker or machine-wide deletion sweep |
| Pick up | Verifies saved work and resumes under current authorization; no dependency on Claude-specific Plan mode tools |
| Session hardening | Evidence-based retrospective; no personal global paths, mandatory agent routing, or automatic policy changes |

## Other ai-tools candidates

- **Logo Gestalt:** good separate design plugin after removing personal installation rules,
  bundling its references/templates, and verifying rendering requirements and attribution.
- **PR babysitting and review cycles:** useful later additions after generalizing host tools,
  bounded polling, reviewer identity, and posting permissions. Not bundled in this release.
- **Branch/worktree cleanup:** useful separately, but destructive scope and host-specific
  workspace management need a dedicated public workflow and recovery validation.
- **Monthly retros/changelogs:** useful after replacing internal `fp` commands and repository
  defaults. Line churn should not be presented as a measurement of human effort.
- **Datadog/Koyeb helpers:** separate integration-focused plugins with documented prerequisites.
- **Production queries, Folio flags, internal Slack routines, personal knowledge-vault routing,
  instruction installation, hooks and launchd:** excluded from this general-purpose toolkit.
- **Slack GIF creator:** excluded here; it carries separate terms that need review before
  redistribution and is outside the developer workflow focus.

## Publication preparation

This package is not yet submission-ready. Confirm publisher identity, country targeting,
commerce/data practices, and the actual website, support, privacy, and terms destinations.
Unknown declarations are deliberately absent from the manifest. A draft policy is not a
published policy, and the account plugin's private visibility is not public directory approval.

The existing starter prompt `Unblock Roadmap` is preserved. The listing now covers all eight
workflows; review prompts demonstrate the additional entry points. The author attribution is
carried over from the existing plugin and does not establish verified publisher status.

No MCP server, demo recording, or reviewer credentials are needed for this skills-only package.
Portal verification, scans, applicable attestations, and submission remain separate steps.
The developer must complete any legal/policy attestations themselves.

## Validation

The build checks package identity, field lengths, public account-binding restrictions,
skill inventory, source portability, and ZIP integrity. YAML/schema and disposable-Git
checks are recorded in `VALIDATION.md`; those do not establish real-host model behavior
or public review approval.
