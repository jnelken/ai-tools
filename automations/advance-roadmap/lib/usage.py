#!/usr/bin/env python3
"""Multi-provider usage handoff for advance-roadmap.

Machine-readable source of truth for the next run's routing. MODEL_ROUTER.md
remains policy; this file is what run.sh actually reads.

Usage:
  usage.py refresh [--root DIR]
  usage.py pick-orchestrator [--root DIR]
  usage.py pick-worker-chain [--root DIR]
  usage.py record-limit --provider P --text T [--scope S] [--root DIR]
  usage.py detect-limit --file PATH | --text T
  usage.py show [--root DIR]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HOME = Path.home()
DEFAULT_ROOT = Path(os.environ.get(
    "ADVANCE_ROADMAP_ROOT",
    HOME / ".claude/automations/advance-roadmap",
))
CLAUDE_USAGE = Path(os.environ.get(
    "ADVANCE_ROADMAP_USAGE_FILE",
    HOME / ".claude/state/claude-usage.json",
))
USAGE_NAME = "providers-usage.json"

MAX_FIVE_HOUR_PCT = 70
MAX_SEVEN_DAY_PCT = 80

LIMIT_PATTERNS = [
    re.compile(r"hit your session limit", re.I),
    re.compile(r"rate.?limit", re.I),
    re.compile(r"usage.?limit", re.I),
    re.compile(r"quota.?exceeded", re.I),
    re.compile(r"you've hit your limit", re.I),
    re.compile(r"out of (?:usage|quota|credits)", re.I),
    re.compile(r"resource[_ ]exhausted", re.I),
]


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def usage_path(root: Path) -> Path:
    return root / USAGE_NAME


def empty_state() -> dict[str, Any]:
    return {
        "updated_at": None,
        "providers": {
            "claude": {
                "five_hour": {"used_pct": None, "reset_at": None, "reset_epoch": None, "source": "unknown"},
                "seven_day": {"used_pct": None, "reset_at": None, "reset_epoch": None, "source": "unknown"},
                "available_for_orchestrator": True,
                "available_for_worker": True,
                "last_limit_hit": None,
            },
            "codex": {
                "five_hour": {"used_pct": None, "reset_at": None, "reset_epoch": None, "source": "unknown"},
                "weekly": {"used_pct": None, "reset_at": None, "reset_epoch": None, "source": "unknown"},
                "available_for_orchestrator": True,
                "available_for_worker": True,
                "last_limit_hit": None,
            },
            "cursor": {
                "available_for_worker": True,
                "source": "assume_until_limit_hit",
                "last_limit_hit": None,
            },
        },
        "history": [],
    }


def load(root: Path) -> dict[str, Any]:
    path = usage_path(root)
    if not path.is_file():
        return empty_state()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty_state()
    base = empty_state()
    base.update({k: v for k, v in data.items() if k in ("updated_at", "history")})
    for name, defaults in base["providers"].items():
        got = (data.get("providers") or {}).get(name) or {}
        merged = dict(defaults)
        merged.update(got)
        base["providers"][name] = merged
    if not isinstance(base.get("history"), list):
        base["history"] = []
    return base


def save(root: Path, state: dict[str, Any]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = now_iso()
    path = usage_path(root)
    path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    return path


def window_exhausted(used_pct: Any, reset_epoch: Any, max_pct: int, now: float) -> bool:
    if used_pct is None:
        return False
    try:
        pct = float(used_pct)
    except (TypeError, ValueError):
        return False
    if reset_epoch:
        try:
            if float(reset_epoch) < now:
                return False
        except (TypeError, ValueError):
            pass
    return pct >= max_pct


def claude_available(prov: dict[str, Any], now: float) -> bool:
    hit = prov.get("last_limit_hit") or {}
    reset_epoch = hit.get("reset_epoch")
    if reset_epoch:
        try:
            if float(reset_epoch) > now:
                return False
        except (TypeError, ValueError):
            pass
    five = prov.get("five_hour") or {}
    seven = prov.get("seven_day") or {}
    if window_exhausted(five.get("used_pct"), five.get("reset_epoch"), MAX_FIVE_HOUR_PCT, now):
        return False
    if window_exhausted(seven.get("used_pct"), seven.get("reset_epoch"), MAX_SEVEN_DAY_PCT, now):
        return False
    return True


def provider_available_from_limit(prov: dict[str, Any], now: float) -> bool:
    hit = prov.get("last_limit_hit") or {}
    reset_epoch = hit.get("reset_epoch")
    if reset_epoch:
        try:
            if float(reset_epoch) > now:
                return False
        except (TypeError, ValueError):
            pass
    if hit.get("observed_at") and not reset_epoch:
        try:
            observed = datetime.fromisoformat(hit["observed_at"]).timestamp()
            if now - observed < 3600:
                return False
        except (TypeError, ValueError):
            pass
    return True


def probe_claude(state: dict[str, Any]) -> None:
    prov = state["providers"]["claude"]
    if not CLAUDE_USAGE.is_file():
        return
    try:
        raw = json.loads(CLAUDE_USAGE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    now = time.time()
    for key_src, key_dst in (("five_hour", "five_hour"), ("seven_day", "seven_day")):
        block = raw.get(key_src) or {}
        reset_epoch = block.get("reset_epoch")
        used = block.get("used_percentage")
        if reset_epoch and float(reset_epoch) < now:
            used = 0
        prov[key_dst] = {
            "used_pct": used,
            "reset_at": block.get("reset_at"),
            "reset_epoch": reset_epoch,
            "source": "probe",
        }


def recompute_flags(state: dict[str, Any]) -> None:
    now = time.time()
    claude = state["providers"]["claude"]
    ok = claude_available(claude, now) and provider_available_from_limit(claude, now)
    claude["available_for_orchestrator"] = ok
    claude["available_for_worker"] = ok

    codex = state["providers"]["codex"]
    ok = provider_available_from_limit(codex, now)
    five = codex.get("five_hour") or {}
    weekly = codex.get("weekly") or {}
    if window_exhausted(five.get("used_pct"), five.get("reset_epoch"), MAX_FIVE_HOUR_PCT, now):
        ok = False
    if window_exhausted(weekly.get("used_pct"), weekly.get("reset_epoch"), MAX_SEVEN_DAY_PCT, now):
        ok = False
    codex["available_for_orchestrator"] = ok
    codex["available_for_worker"] = ok

    cursor = state["providers"]["cursor"]
    cursor["available_for_worker"] = provider_available_from_limit(cursor, now)


def refresh(root: Path) -> dict[str, Any]:
    state = load(root)
    probe_claude(state)
    recompute_flags(state)
    save(root, state)
    return state


def pick_orchestrator(state: dict[str, Any]) -> str | None:
    """Prefer Codex Sol; fall back to Claude Opus. None if both hot."""
    if state["providers"]["codex"].get("available_for_orchestrator"):
        return "codex"
    if state["providers"]["claude"].get("available_for_orchestrator"):
        return "claude"
    return None


def pick_worker_chain(state: dict[str, Any]) -> list[str]:
    """Cursor → Codex → Claude, skipping exhausted providers."""
    chain: list[str] = []
    if state["providers"]["cursor"].get("available_for_worker"):
        chain.append("cursor")
    if state["providers"]["codex"].get("available_for_worker"):
        chain.append("codex")
    if state["providers"]["claude"].get("available_for_worker"):
        chain.append("claude")
    return chain


def parse_reset_epoch_from_text(text: str, observed: float) -> int | None:
    m = re.search(r"resets?\s+in\s+(\d+)\s*h(?:ours?)?\s*(\d+)?\s*m?", text, re.I)
    if m:
        hours = int(m.group(1))
        mins = int(m.group(2) or 0)
        return int(observed + hours * 3600 + mins * 60)
    m = re.search(r"resets?\s+in\s+(\d+)\s*m", text, re.I)
    if m:
        return int(observed + int(m.group(1)) * 60)
    return None


def record_limit(
    root: Path,
    provider: str,
    text: str,
    scope: str = "unknown",
) -> dict[str, Any]:
    state = load(root)
    if provider not in state["providers"]:
        raise SystemExit(f"unknown provider: {provider}")
    observed = time.time()
    reset_epoch = parse_reset_epoch_from_text(text, observed)
    hit = {
        "observed_at": now_iso(),
        "scope": scope,
        "exact_cli_text": text.strip()[:500],
        "reset_epoch": reset_epoch,
        "reset_source": "derived" if reset_epoch else "unknown",
    }
    state["providers"][provider]["last_limit_hit"] = hit
    state["history"].append({
        "observed_at": hit["observed_at"],
        "provider": provider,
        "scope": scope,
        "exact_cli_text": hit["exact_cli_text"],
        "routing_decision": f"bypass {provider} until reset",
    })
    state["history"] = state["history"][-40:]
    recompute_flags(state)
    save(root, state)
    return state


def detect_limit(text: str) -> bool:
    return any(p.search(text or "") for p in LIMIT_PATTERNS)


def cmd_refresh(args: argparse.Namespace) -> None:
    state = refresh(Path(args.root))
    orch = pick_orchestrator(state)
    workers = pick_worker_chain(state)
    print(json.dumps({
        "orchestrator": orch,
        "workers": workers,
        "path": str(usage_path(Path(args.root))),
        "providers": {
            name: {
                "orchestrator": p.get("available_for_orchestrator"),
                "worker": p.get("available_for_worker"),
            }
            for name, p in state["providers"].items()
        },
    }, indent=2))


def cmd_pick_orchestrator(args: argparse.Namespace) -> None:
    state = refresh(Path(args.root))
    orch = pick_orchestrator(state)
    if not orch:
        print("none")
        sys.exit(2)
    print(orch)


def cmd_pick_worker_chain(args: argparse.Namespace) -> None:
    state = refresh(Path(args.root))
    chain = pick_worker_chain(state)
    if not chain:
        print("")
        sys.exit(2)
    print(" ".join(chain))


def cmd_record_limit(args: argparse.Namespace) -> None:
    record_limit(Path(args.root), args.provider, args.text, args.scope)
    print("ok")


def cmd_detect(args: argparse.Namespace) -> None:
    text = args.text
    if args.file:
        text = Path(args.file).read_text(encoding="utf-8", errors="replace")
    sys.exit(0 if detect_limit(text or "") else 1)


def cmd_show(args: argparse.Namespace) -> None:
    print(json.dumps(load(Path(args.root)), indent=2))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(DEFAULT_ROOT))
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("refresh")
    p.set_defaults(func=cmd_refresh)

    p = sub.add_parser("pick-orchestrator")
    p.set_defaults(func=cmd_pick_orchestrator)

    p = sub.add_parser("pick-worker-chain")
    p.set_defaults(func=cmd_pick_worker_chain)

    p = sub.add_parser("record-limit")
    p.add_argument("--provider", required=True, choices=["claude", "codex", "cursor"])
    p.add_argument("--text", required=True)
    p.add_argument("--scope", default="unknown")
    p.set_defaults(func=cmd_record_limit)

    p = sub.add_parser("detect-limit")
    p.add_argument("--text", default="")
    p.add_argument("--file")
    p.set_defaults(func=cmd_detect)

    p = sub.add_parser("show")
    p.set_defaults(func=cmd_show)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
