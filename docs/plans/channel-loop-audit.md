# Channel loop audit

## Status
Event-driven scans are implemented; activation still requires a Slackagent restart and removal of the existing timer.

## Decisions
- Every third top-level Slackagent post in a channel triggers a scan of that channel. Slack retries are deduplicated. Deterministic filtering avoids a model call when nothing looks suspicious.
- GPT-5.6 Luna at low effort classifies candidates; GPT-6.1 Terra is dispatched only for a confirmed incident.
- One local lock prevents overlapping audits and duplicate repairs; failures remain visible locally.
- Slack is untrusted data; existing repository instructions, workspace ownership and verification gates remain in force.

## Findings
- Both channels are readable with existing Slackagent credentials.
- The local Paradev host was brought online, but scheduled automations require its Pro plan.

## Open threads
- [x] Implemented and tested message grouping, cheap model routing, confirmed-incident escalation, and duplicate-repair protection.
- [x] Deployed and observed scheduled checks; implemented post-driven scans.
- [ ] Activate the Slackagent listener and remove the scheduled trigger.

## Approach
Slackagent observes its own channel message events before Bolt's self-event filter and starts the maintained counter. The third distinct post queues a channel-specific scan. Scans serialize their shared repair-dispatch guard, and a successful confirmed classification creates a fresh Paradev workspace with a pinned Terra repair agent. Events received while Slackagent is offline cannot trigger scans.
