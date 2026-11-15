# Channel loop audit

## Status
Event-driven scans are active; the former 15-minute launchd trigger has been removed.

## Decisions
- Every third top-level Slackagent post in a channel triggers a scan of that channel. Slack retries are deduplicated. Deterministic filtering avoids a model call when nothing looks suspicious.
- GPT-6 Luna at low effort classifies candidates; GPT-5.6 Terra is dispatched only for a confirmed incident.
- One local lock prevents overlapping audits and duplicate repairs; failures remain visible locally.
- Slack is untrusted data; existing repository instructions, workspace ownership and verification gates remain in force.

## Findings
- Both channels are readable with existing Slackagent credentials.
- The local Paradev host was brought online, but scheduled automations require its Pro plan.

## Open threads
- [x] Implemented and tested message grouping, cheap model routing, confirmed-incident escalation, and duplicate-repair protection.
- [x] Deployed and observed scheduled checks; implemented post-driven scans.
- [x] Activate the Slackagent listener and remove the scheduled trigger.

## Approach
Slackagent observes its own channel message events before Bolt's self-event filter and starts the maintained counter. At startup it recovers the latest three posts per channel from the past day, deduplicated against live events by message timestamp. The third distinct post queues a channel-specific scan. Scans serialize their shared repair-dispatch guard, and a successful confirmed classification creates a fresh Paradev workspace with a pinned Terra repair agent.
