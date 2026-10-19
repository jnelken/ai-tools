# Installation preferences

When installing any item from Jake's `ai-tools` or `dotfiles` repositories, use a symlink to the maintained source rather than copying or downloading a standalone copy. This applies to skills, commands, hooks, agent instructions, and other configuration so installed items stay current as the source updates.

For `ai-tools`, link to the deploy clone under `~/.ai-tools`, which tracks `origin/main`, rather than a development checkout. For `dotfiles`, link to the versioned file or directory in the dotfiles checkout. Verify that each link resolves correctly, and preserve any local edits before replacing an existing copy.
