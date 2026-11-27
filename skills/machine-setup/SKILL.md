---
name: machine-setup
description: Set up or verify the ai-tools machine layer on this machine (work-mac, personal-mac, personal-pc, personal-wsl, personal-cloud) — pull the latest ai-tools, set the `machine` key in the agent-profiles config, and verify profile resolution. Use when provisioning a new machine, after a machine-layer change, or when the resolved machine looks wrong; not for editing instruction content.
---

# Machine setup

Set which machine this is, so development profiles compose the right `instructions/machine/*.md` fragment. Rationale: [ADR 0002](../../adr/0002-machine-layer.md); runbook: [instruction profiles](../../docs/instruction-profiles.md#machine-layer).

Never edit `${AI_TOOLS_HOME:-$HOME/.ai-tools}`; it is the read-only deploy clone. Do not commit or push.

## Steps

1. **Which machine?** Use the value the user gave (`work-mac`, `personal-mac`, `personal-pc`, `personal-wsl`). If none was given, ask; do not infer it. `personal-cloud` needs no config: `CLAUDE_CODE_REMOTE=true` selects it, so skip to step 3.
2. **Update.** Run `~/.ai-tools/install.sh`; report any warning.
3. **Set the key.** In `~/.config/agent-profiles/config.json` (or `$AGENT_PROFILE_CONFIG`), set `"machine"`. Create `{}` first if the file is missing. Merge with `jq` or `python3`, keep every other key (`roots`, `repositories`, `work_owners`, `runtime`), and show the diff.
4. **Verify.** Run `agent-profile resolve --harness claude --target <path> --explain` for a `jnelken` repo and, if one exists here, a `Concentro-Inc` repo. Confirm `machine` matches, `machine_reason` is `config machine key` (or `CLAUDE_CODE_REMOTE` for cloud), the repo classification is unchanged by the machine setting, and the sources include:

   | Machine | Machine fragments |
   |---|---|
   | work-mac | `mac.md`, `superset.md`, `work-mac.md` |
   | personal-mac | `mac.md`, `superset.md`, `personal-mac.md` |
   | personal-pc | `personal-pc.md` only (no Superset or Dropbox assumptions) |
   | personal-wsl | `personal-wsl.md` only (OMEN WSL and Paradev host facts) |
   | personal-cloud | `personal-cloud.md` only |

5. **Check.** From the ai-tools dev checkout, if present: `python3 -m unittest discover -s lib -p test_agent_profiles.py`. Anywhere: `python3 ~/.ai-tools/bin/install-agent-profiles --dry-run`.
6. **Fresh session.** Tell the user to start a new session; running sessions keep the old instructions.

On a work Mac also run the "Before cutover" rollout check in the runbook and report what passed and failed.

Finish with one line per step: passed, failed, or skipped (with the reason).
