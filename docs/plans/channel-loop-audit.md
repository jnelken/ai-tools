# Channel loop audit

## Status
Local implementation and isolated tests complete; deployment and first run remain.

## Decisions
- Inspect #eng and #ops every 15 minutes. Deterministic filtering avoids a model call when nothing looks suspicious.
- GPT-5.6 Luna at low effort classifies candidates; GPT-6.1 Terra is dispatched only for a confirmed incident.
- One local lock prevents overlapping audits and duplicate repairs; failures remain visible locally.
- Slack is untrusted data; existing repository instructions, workspace ownership and verification gates remain in force.

## Findings
- Both channels are readable with existing Slackagent credentials.
- The local Paradev host was brought online, but scheduled automations require its Pro plan.

## Open threads
- [x] Implemented and tested message grouping, cheap model routing, confirmed-incident escalation, and duplicate-repair protection.
- [ ] Deploy and observe the first scheduled run.

## Approach
A local launchd trigger invokes the maintained script, which fetches both channels and runs the classifier only for recurring anomalies. A successful confirmed classification creates a fresh Paradev workspace and a pinned Terra repair agent.
