## Personal WSL (OMEN)

This environment is Ubuntu under WSL on the personal Windows PC OMEN. Prefer the Linux filesystem for Linux development: personal checkouts live under `~/code`, dotfiles under `~/dotfiles`, and the ai-tools deploy clone at `~/.ai-tools`. Install ai-tools configuration from the deploy clone, not a development checkout.

Paradev (formerly Superset) is installed on this machine. The standalone CLI is `~/superset/bin/superset`; its J3u host is named OMEN. The `paradev-j3u-host.service` systemd service manages the host, and a Windows sign-in task starts WSL. The machine must be awake for remote access.

This is Linux: macOS-only paths, Homebrew locations, and launchd services do not describe this environment. Machine facts do not grant repository permissions; resolve each target repository separately.
