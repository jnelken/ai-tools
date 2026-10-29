Oct 27 batch, koan-master / lunchmoney / openclaw-vps: a Claude planner finished its triage and
emitted the ORCHESTRATOR_RESULT_JSON fence, then the Stop hook (hooks/status-glyph-check.sh) forced one
more turn whose only text was the `💬 …` sign-off. `claude -p --output-format json` returns only that
last message as `result`, so run.sh found no fence and recorded `error` ($9.21 of finished triage lost).

`claude-planner-transcript.jsonl` is that session's shape, hand-trimmed: the prompt echo is a *user*
entry whose example fence is a valid but wrong plan; the real fence is in the assistant message before
the sign-off; a sidechain entry quotes another fence.

The repo in the fixture is `demo`, the replay harness's target repo (a planner's bookkeeping for any
other repo is dropped by run.sh, which would hide what the test is checking).
