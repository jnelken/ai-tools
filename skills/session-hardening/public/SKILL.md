---
name: session-hardening
description: Review a coding session's actual tooling friction and propose durable fixes to instructions, scripts, documentation, or workflow. Use for a session retrospective rather than a general code-quality audit.
---

# Session Hardening

Ask what slowed this session down and what concrete change would prevent a recurrence.
Use the conversation and observed failures as evidence. This is not an automatic code
cleanup or a search for hypothetical improvements; a session with no meaningful friction
can finish with no findings.

Look for stale or missing knowledge, misfiring hooks or permissions, repeated manual
work, fragile tooling, misleading names, and mechanisms the completed work made obsolete.
For each candidate, identify the actual failed command, correction, or repetition and
trace it to a cause. Check existing docs/tools before proposing a new mechanism.

Report each supported finding with:

- The observed friction and evidence.
- Its likely cause and the smallest durable fix.
- Whether the fix belongs to this repository, shared tooling, or a particular host.
- How to verify it and any scope or compatibility risk.

If the user asked for fixes, apply small reversible corrections within the authorized
scope and verify them. Present shared policy changes, behavior-changing automation, and
deletions as concrete proposals when they need a user choice. Merely requesting a
retrospective does not authorize changing global agent instructions or account settings.

Record intentional mechanisms that looked redundant but remain necessary. Prove callers
and purpose before deleting anything; a rarely used guard is not automatically dead.
Prefer fixing the existing source of truth over adding a second instruction layer.

Save remaining findings in the user's chosen repository document or existing handoff
note when authorized. Use an external issue tracker only when available and authorized,
checking for duplicates. Never invent a required tracker, a local memory path, or a
session transcript that the host cannot access.

Finish with fixes applied, verification, and remaining proposals. Keep a self-started
observation report-only unless the user has authorized remediation.
