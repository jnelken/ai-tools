---
name: deploy-koyeb
description: Use when deploying any local service directory to a Koyeb app/service from a worktree or local branch, before or after opening a PR.
---

# Deploy to Koyeb

## Overview
Deploy a local service directory to Koyeb using the CLI. No registry push needed — Koyeb builds from the local directory.

## Quick Reference

| Service type | Deploy command | Verify |
|---|---|---|
| Any | `koyeb deploy <path> <app>/<service> --wait` | `koyeb services get <app>/<service>` |
| WEB | same | `curl -i --fail <health-url>` then `koyeb services get` |
| WORKER | same | `koyeb services get` only (no HTTP endpoint) |

## Script

`scripts/deploy.sh` runs deploy + verification in one step:

```bash
scripts/deploy.sh <service-path> <app/service> [health-url]
```

- `health-url` is optional — omit for WORKER services.

Example (WEB service with health check):
```bash
scripts/deploy.sh /path/to/repo/services/my-svc personal-services/my-svc https://personal-services.koyeb.app/health
```

Example (WORKER, no health URL):
```bash
scripts/deploy.sh /path/to/repo/services/sparky personal-services/sparky
```

## Prerequisites
- `koyeb` CLI installed and authenticated (`koyeb whoami`)

## Notes
- `--wait` blocks until the deployment succeeds or fails — always use it
- Deployment does not replace PR review/merge; ship from branch, then merge
