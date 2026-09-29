---
name: jnelken-linear
description: Create or relabel an issue in Jake's personal Linear workspace (`jnelken`, team Dev, keys DEV-*) — the only sanctioned way to file one, because it refuses to create an issue without a `repo/<directory>` label and infers that label from the checkout you're in. Use whenever filing a ticket about a personal repo under the personal code dir, capturing an idea or bug for one of Jake's side projects, fixing an issue that's missing its repo label, or when advance-roadmap reports "has no repo/* label". Not for Concentro (CON-*) tickets — those use [[linear-ticket-gen]].
---

# Filing jnelken Linear issues

Every Dev issue carries exactly one `repo/<directory>` label. [[advance-roadmap]] reads it to decide
which checkout under the personal code dir (`~/.ai-tools/bin/personal-code-dir`) to write code in and push, so:

- **no label** → the issue is invisible to automation (it sits as a "which repository?" blocker);
- **wrong label** → code gets pushed to the wrong repo.

Linear can't make a label required, so `jlin.py` does. **Never create a Dev issue any other way** —
not `linear issue create`, not the Linear MCP `save_issue`, not raw `issueCreate`. In 2026-09 an
agent that didn't know this filed eight unlabeled issues in one sitting.

## Create

```bash
cd "$(~/.ai-tools/bin/personal-code-dir)/<repo>"   # repo is inferred from the checkout you're in
python3 ~/.claude/skills/jnelken-linear/jlin.py new --title "Imperative title" \
  --description-file /path/to/body.md [--priority 1-4] [--state backlog|todo]
```

Outside a checkout, pass `--repo <directory>` (a directory name under the personal code dir, or its label
name). Always `--dry-run` first when unsure — it prints the payload, including which label it chose.

- **Repo unknown?** Ask Jake. `jlin.py` exits rather than filing, on purpose. Don't pick the "closest"
  repo, and don't file under a repo you're merely *in* when the idea belongs elsewhere — pass `--repo`.
- **Directory exists but has no label yet?** `new` creates `repo/<directory>` under the `repo` group,
  with the GitHub origin (or "Local-only") as its description.
- **Defaults:** team Dev, state Backlog, repo label set at creation. Team Dev has triage off, so the
  issue will not be hidden in Triage.
- **Content conventions** (duplicate check first, evidence-grade body) are in [[linear-ticket-gen]];
  this skill only owns *where* the issue lands.

## Fix a missing or wrong label

```bash
python3 ~/.claude/skills/jnelken-linear/jlin.py label DEV-123 --repo mailcrush \
  --evidence "the body names the Mailcrush inbox"
```

Replaces any existing `repo/*` child (the group is single-select). `--evidence` posts a comment so
Jake can see why, and correct it.

## Inference for issues that arrive unlabeled

```bash
python3 ~/.claude/skills/jnelken-linear/jlin.py infer            # dry run over every open unlabeled issue
python3 ~/.claude/skills/jnelken-linear/jlin.py infer --apply    # label the unambiguous ones
```

It only labels an issue whose title or body names **exactly one** repo label as a whole word, and
comments the evidence. Anything else stays unlabeled for a human; advance-roadmap runs this
before each run, so mentions of a repo by name get picked up automatically. Topic words ("inbox",
"STT", "Tidbyt") are deliberately not matched — too easy to guess wrong.

`jlin.py repos` lists every directory and its label (or `(no label yet)`).

## Mechanics

Uses the `linear` CLI's `linear api` (auth: `LINEAR_API_TOKEN`), never MCP — see [[linear-cli]].
Offline tests: `python3 -m unittest discover -s ~/.claude/skills/jnelken-linear`.
