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
