Loaded on demand from `instructions/development/work.md` — read before pulling, rotating, moving or writing an env-var secret.

## Secrets and `.env` files

The 1Password vault `ENV` (concentro.1password.com) is the source of truth for env-var secrets. Each variable is its own API Credential item, titled with the exact variable name, with the value in its `credential` field. Each item is tagged `woodrow`, `api`, both, or `scripts` (run ad hoc, not needed for the app to run). Page logins stay in `Engineering`.

- **Work from local, git-ignored `.env` files, not 1Password.** Put each value in the `.env` of the repo that reads it. Company-wide scripts and automations use `internal-tools/.env`, or `folio-platform/.env` when the tool is platform or observability tooling (Datadog, monitors, deploys), whichever fits.
- **New or rotated value, on a Mac Jake is sitting at:** pull it from `ENV` in one batched `op` run and write it to the matching `.env`. Each batch needs Jake to approve 1Password on that machine, and the approval lapses after about 10 idle minutes. Never print values.
- **On an agent host (jake2, any Mac driven remotely), never run `op`.** The approval needs Touch ID at the keyboard and an unlocked screen, so the call only times out, and asking Jake to approve it there cannot work. `dotfiles-role` says whether a Mac is an agent host; a `bin/op` shim on `PATH` blocks the call there and prints these steps. Instead:
  1. Jake, on the Mac he is at, one command for any number of names: `zsh-secrets scratch pull <NAME> [<NAME>...]`. It runs the batched `op read` from `ENV`, stores each value in scratch (`--ttl` to change the 7d default) and prints the agent-side command. Claude cannot trigger the Touch ID prompt, so Jake runs this himself; the value never goes on a command line.
  2. The agent, on the host, once `zsh-secrets scratch ls` shows `<NAME>`: `zsh-secrets scratch put <repo>/.env <NAME>`, or `zsh-secrets scratch exec --only <NAME> -- <cmd>` to inject it into one command without a file.
  If the value is not in scratch yet (`put` and `exec` say so and print the `pull` command), stop and ask Jake to run step 1 with the exact names. Do not retry `op` or loop on a timeout.
- **Between Macs:** move a value with `zsh-secrets scratch` (`pull NAME` from 1Password, or `set NAME` for a value that is not in `ENV`, on one Mac; `put <repo>/.env NAME` on the other). Shell-wide keys live in `~/.zsh_secrets`, synced by `zsh-secrets push`/`pull`.
