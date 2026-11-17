## Personal cloud session

This is a Linux cloud container (`CLAUDE_CODE_REMOTE=true`). Repositories are checked out as siblings under `/home/user/<repo>`, and the container is ephemeral: commit and push before ending. There is no Dropbox personal code dir, Superset or launchd here, so guidance that depends on them (Lunch Money cache, ROADMAP exemplar paths, sswt) does not apply. ai-tools is installed from the environment's setup script into `~/.ai-tools`; edit skills and instructions in the cloned `ai-tools` repo, never in the deploy clone.
