# Roadmap Slack decisions and scheduling

## Status

 Implementation and focused verification complete; deployment is being coordinated with the live Slackagent session.

## Decisions

- Use a concise headline and bulleted facts for automation notifications; omit a redundant no-worker line.
- Route an owner's blocking decision to the existing resumable worker when one exists; otherwise start targeted planning rather than claim a nonexistent worker was resumed.
- Never infer that a ticket is actionable solely because an unstructured dependency is absent from Linear's formal blocker fields.

## Findings

- The November 17 tidbyt-api no-item run found DEV-276 was code-health and DEV-227 needed a decision about DEV-223; no worker was assigned (`~/.claude/automations/advance-roadmap/runs/20261117-223903/`).
- The initial selection saw DEV-276 as eligible and DEV-227's dependency was not a formal Linear blocker.
- The saved ticket data cannot reliably prove every prerequisite mentioned only in prose. The scheduler filters known ineligible labels and backs off verified no-work findings, while rechecking changed ticket inputs, repo HEAD, or policy.
- A worker with a recorded continuation must go through the existing workqueue and its session/checkout checks. A planner-only run has no worker session to resume.

## Open threads

- [x] Correct scheduler eligibility and avoid repeatedly selecting known nonactionable candidates; first-time prose-only dependencies still require planner judgment.
- [x] Reformat notifications from available evidence, including actionable blocking questions.
- [x] Route authorized thread replies through verified Linear directives to the durable scheduler, resuming an exact saved assignment or planning the selected ticket.
- [x] Verify scheduler and Slack handling with offline tests and replay the original no-work summary.
- [ ] Deploy the ai-tools clone and restart Slackagent after the current investigation reply has been delivered.

## Approach

Trace existing scheduling, notification, and session recovery owners. Add focused regression coverage for eligibility, notification facts, and reply routing, then run the affected suites and deploy both repositories.
