# Shared Codex defaults for shell entrypoints and the Python plan approver.
CODEX_MODEL_DEFAULT=gpt-6.1-sol
# Cheap/fast tier for roles that do not need the best model.
CODEX_FAST_MODEL_DEFAULT=gpt-6-luna
# Claude as planner and plan approver: triage reads and ranks, it doesn't write code, so Sonnet at
# medium effort (Opus at high cost ~$2.60 per no-work planner on 2026-10-27). Workers and reviewers
# keep their own models. Override with ADVANCE_ROADMAP_ORCH_CLAUDE_MODEL / _ORCH_CLAUDE_EFFORT.
# Bare NAME=value lines: planapproval.py reads this file with a regex.
CLAUDE_PLANNER_MODEL_DEFAULT=claude-sonnet-5-5
CLAUDE_PLANNER_EFFORT_DEFAULT=medium
# Workers write the code: Sonnet at high effort on Claude, Luna at high effort on Codex (2026-11-07).
# Opus's estimated worker cost exceeded the Claude five-hour cap, and the ChatGPT-account Codex
# rejects gpt-6.1-sol (model_not_found). Override with ADVANCE_ROADMAP_{CLAUDE,CODEX}_WORKER_MODEL
# and _WORKER_EFFORT.
CLAUDE_WORKER_MODEL_DEFAULT=claude-sonnet-5-5
CLAUDE_WORKER_EFFORT_DEFAULT=high
CODEX_WORKER_MODEL_DEFAULT=gpt-6-luna
CODEX_WORKER_EFFORT_DEFAULT=high
