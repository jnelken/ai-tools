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
  usage.py probe [--provider codex|cursor|all]   # live read, writes nothing
  usage.py show [--root DIR]

Sources (see automations/README.md "Provider usage sources"):
  claude  ~/.claude/state/claude-usage.json (statusline)
  codex   `codex app-server` account/rateLimits/read, else newest
          ~/.codex/sessions rollout token_count rate_limits
  cursor  DashboardService/GetCurrentPeriodUsage with the Agent CLI's
          Keychain token (never persisted)
Every source is best-effort; observed limit hits remain the fallback.
"""
from __future__ import annotations

import argparse
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
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

CODEX_BIN = os.environ.get("ADVANCE_ROADMAP_CODEX_BIN", "/opt/homebrew/bin/codex")
CODEX_SESSIONS = Path(os.environ.get(
    "ADVANCE_ROADMAP_CODEX_SESSIONS",
    HOME / ".codex/sessions",
))
CURSOR_ENDPOINT = os.environ.get("ADVANCE_ROADMAP_CURSOR_ENDPOINT", "https://api2.cursor.sh")
CURSOR_KEYCHAIN_SERVICE = os.environ.get(
    "ADVANCE_ROADMAP_CURSOR_KEYCHAIN_SERVICE", "cursor-access-token")
CURSOR_KEYCHAIN_ACCOUNT = "cursor-user"

MAX_FIVE_HOUR_PCT = 70
MAX_SEVEN_DAY_PCT = 80
# Cursor quotas are monthly and worker.sh fails over within the tick, so gate
# only when the pool is effectively spent.
MAX_CURSOR_PCT = 95

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
                "included": {
                    "auto_pct": None, "api_pct": None, "total_pct": None,
                    "reset_at": None, "reset_epoch": None,
                    "source": "unknown", "observed_at": None,
                },
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


def probes_enabled() -> bool:
    return os.environ.get("ADVANCE_ROADMAP_USAGE_PROBES", "1") != "0"


def epoch_to_iso(epoch: Any) -> str | None:
    if epoch is None:
        return None
    try:
        return datetime.fromtimestamp(float(epoch), timezone.utc).astimezone().isoformat(timespec="seconds")
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _pick(d: dict[str, Any], *keys: str) -> Any:
    for k in keys:
        if d.get(k) is not None:
            return d[k]
    return None


def _codex_window(block: Any) -> dict[str, Any] | None:
    if not isinstance(block, dict):
        return None
    pct = _num(_pick(block, "usedPercent", "used_percent"))
    reset = _num(_pick(block, "resetsAt", "resets_at"))
    mins = _num(_pick(block, "windowDurationMins", "window_minutes"))
    if pct is None:
        return None
    reset_epoch = int(reset) if reset is not None else None
    return {
        "used_pct": pct,
        "reset_epoch": reset_epoch,
        "reset_at": epoch_to_iso(reset_epoch),
        "window_minutes": int(mins) if mins is not None else None,
    }


def parse_codex_rate_limits(obj: Any) -> dict[str, Any] | None:
    """Normalize app-server (camelCase) or session-log (snake_case) rate limits.

    Only the `codex` limit id is read; other pools (e.g. base_model_inference)
    are ignored. Windows are assigned by duration, falling back to position.
    """
    if not isinstance(obj, dict):
        return None
    by_id = obj.get("rateLimitsByLimitId")
    if isinstance(by_id, dict) and isinstance(by_id.get("codex"), dict):
        rl = by_id["codex"]
    elif isinstance(obj.get("rateLimits"), dict):
        rl = obj["rateLimits"]
    elif isinstance(obj.get("rate_limits"), dict):
        rl = obj["rate_limits"]
    else:
        rl = obj
    if _pick(rl, "limitId", "limit_id") not in (None, "codex"):
        return None
    windows: dict[str, Any] = {}
    for default_key, raw in (("five_hour", rl.get("primary")), ("weekly", rl.get("secondary"))):
        w = _codex_window(raw)
        if not w:
            continue
        mins = w["window_minutes"]
        key = default_key if mins is None else ("five_hour" if mins <= 24 * 60 else "weekly")
        windows[key] = w
    if not windows:
        return None
    reached = _pick(rl, "rateLimitReachedType", "rate_limit_reached_type")
    plan = _pick(rl, "planType", "plan_type")
    return {
        "five_hour": windows.get("five_hour"),
        "weekly": windows.get("weekly"),
        "reached": str(reached) if reached is not None else None,
        "plan": str(plan) if plan is not None else None,
    }


def parse_cursor_usage(obj: Any) -> dict[str, Any] | None:
    """Normalize a GetCurrentPeriodUsage response to whitelisted numbers.

    `displayMessage` and the dollar pool are deliberately not used for gating:
    they report "limit hit" while requests still succeed on bonus usage.
    """
    if not isinstance(obj, dict) or not isinstance(obj.get("planUsage"), dict):
        return None
    pu = obj["planUsage"]
    end_ms = _num(obj.get("billingCycleEnd"))
    reset_epoch = int(end_ms / 1000) if end_ms else None
    out = {
        "auto_pct": _num(pu.get("autoPercentUsed")),
        "api_pct": _num(pu.get("apiPercentUsed")),
        "total_pct": _num(pu.get("totalPercentUsed")),
        "included_spend_cents": _num(pu.get("includedSpend")),
        "limit_cents": _num(pu.get("limit")),
        "bonus_spend_cents": _num(pu.get("bonusSpend")),
        "reset_epoch": reset_epoch,
        "reset_at": epoch_to_iso(reset_epoch),
    }
    if out["auto_pct"] is None and out["api_pct"] is None and out["total_pct"] is None:
        return None
    return out


def probe_codex_app_server(timeout: float = 20.0) -> dict[str, Any] | None:
    """Ask a short-lived `codex app-server` for account/rateLimits/read."""
    if not probes_enabled():
        return None
    binary = CODEX_BIN if os.access(CODEX_BIN, os.X_OK) else shutil.which("codex")
    if not binary:
        return None
    try:
        proc = subprocess.Popen(
            [binary, "app-server"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True,
        )
    except OSError:
        return None
    lines: queue.Queue[str | None] = queue.Queue()

    def pump() -> None:
        try:
            for line in proc.stdout:  # type: ignore[union-attr]
                lines.put(line)
        except (OSError, ValueError):
            pass
        lines.put(None)

    threading.Thread(target=pump, daemon=True).start()
    try:
        msgs = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"clientInfo": {"name": "advance-roadmap-usage", "version": "1"}}},
            {"jsonrpc": "2.0", "method": "initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "account/rateLimits/read"},
        ]
        proc.stdin.write("".join(json.dumps(m) + "\n" for m in msgs))  # type: ignore[union-attr]
        proc.stdin.flush()  # type: ignore[union-attr]
        deadline = time.monotonic() + timeout
        while (left := deadline - time.monotonic()) > 0:
            try:
                line = lines.get(timeout=left)
            except queue.Empty:
                return None
            if line is None:
                return None
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg.get("id") != 2:
                continue
            parsed = parse_codex_rate_limits(msg.get("result"))
            if parsed:
                parsed["source"] = "app_server"
                parsed["observed_epoch"] = time.time()
            return parsed
        return None
    except (OSError, ValueError):
        return None
    finally:
        try:
            proc.kill()
            proc.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            pass


def probe_codex_session_log(max_files: int = 5) -> dict[str, Any] | None:
    """Last codex rate_limits snapshot from the newest rollout logs.

    Only as fresh as the last Codex turn; observed_epoch is the event time.
    """
    if not probes_enabled():
        return None
    try:
        days = sorted((d for d in CODEX_SESSIONS.glob("*/*/*") if d.is_dir()), reverse=True)[:3]
        files = sorted(
            (f for d in days for f in d.glob("rollout-*.jsonl")),
            key=lambda f: f.stat().st_mtime, reverse=True,
        )[:max_files]
    except OSError:
        return None
    best: dict[str, Any] | None = None
    for f in files:
        try:
            fh = f.open(encoding="utf-8", errors="replace")
        except OSError:
            continue
        with fh:
            for line in fh:
                if '"rate_limits"' not in line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                payload = ev.get("payload") if isinstance(ev, dict) else None
                if not isinstance(payload, dict):
                    continue
                parsed = parse_codex_rate_limits({"rate_limits": payload.get("rate_limits")})
                if not parsed:
                    continue
                try:
                    ts = datetime.fromisoformat(str(ev.get("timestamp")).replace("Z", "+00:00")).timestamp()
                except ValueError:
                    ts = f.stat().st_mtime
                if best is None or ts > best["observed_epoch"]:
                    parsed["source"] = "session_log"
                    parsed["observed_epoch"] = ts
                    best = parsed
    return best


def read_cursor_token() -> str | None:
    try:
        out = subprocess.run(
            ["security", "find-generic-password",
             "-s", CURSOR_KEYCHAIN_SERVICE, "-a", CURSOR_KEYCHAIN_ACCOUNT, "-w"],
            capture_output=True, text=True, timeout=5, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    token = out.stdout.strip()
    return token if out.returncode == 0 and token else None


def probe_cursor(timeout: float = 15.0) -> dict[str, Any] | None:
    """GetCurrentPeriodUsage — the RPC behind the Agent CLI's /usage command."""
    if not probes_enabled():
        return None
    token = read_cursor_token()
    if not token:
        return None
    req = urllib.request.Request(
        f"{CURSOR_ENDPOINT}/aiserver.v1.DashboardService/GetCurrentPeriodUsage",
        data=b"{}",
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Connect-Protocol-Version": "1",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        return None
    parsed = parse_cursor_usage(body)
    if parsed:
        parsed["source"] = "dashboard_api"
        parsed["observed_epoch"] = time.time()
    return parsed


def _zero_if_reset(window: dict[str, Any] | None, now: float) -> dict[str, Any] | None:
    if not window:
        return window
    w = dict(window)
    if w.get("reset_epoch") and float(w["reset_epoch"]) < now:
        w["used_pct"] = 0
    return w


def apply_codex(state: dict[str, Any], parsed: dict[str, Any] | None) -> bool:
    """Merge a codex probe unless it is older than what is already stored."""
    if not parsed:
        return False
    prov = state["providers"]["codex"]
    stored = _num(prov.get("observed_epoch")) or 0
    if parsed["observed_epoch"] < stored:
        return False
    now = time.time()
    for key in ("five_hour", "weekly"):
        w = _zero_if_reset(parsed.get(key), now)
        if w:
            prov[key] = {
                "used_pct": w["used_pct"],
                "reset_at": w["reset_at"],
                "reset_epoch": w["reset_epoch"],
                "source": parsed["source"],
            }
    prov["rate_limit_reached"] = parsed.get("reached")
    prov["plan"] = parsed.get("plan")
    prov["observed_epoch"] = parsed["observed_epoch"]
    prov["observed_at"] = epoch_to_iso(parsed["observed_epoch"])
    prov["source"] = parsed["source"]
    return True


def probe_codex(state: dict[str, Any]) -> None:
    if not apply_codex(state, probe_codex_app_server()):
        apply_codex(state, probe_codex_session_log())


def apply_cursor(state: dict[str, Any], parsed: dict[str, Any] | None) -> bool:
    if not parsed:
        return False
    prov = state["providers"]["cursor"]
    inc = {k: parsed.get(k) for k in (
        "auto_pct", "api_pct", "total_pct", "included_spend_cents",
        "limit_cents", "bonus_spend_cents", "reset_at", "reset_epoch")}
    inc["source"] = parsed["source"]
    inc["observed_at"] = epoch_to_iso(parsed["observed_epoch"])
    prov["included"] = inc
    prov["source"] = parsed["source"]
    return True


def probe_cursor_into(state: dict[str, Any]) -> None:
    apply_cursor(state, probe_cursor())


def codex_quota_ok(prov: dict[str, Any], now: float) -> bool:
    five = prov.get("five_hour") or {}
    weekly = prov.get("weekly") or {}
    if window_exhausted(five.get("used_pct"), five.get("reset_epoch"), MAX_FIVE_HOUR_PCT, now):
        return False
    if window_exhausted(weekly.get("used_pct"), weekly.get("reset_epoch"), MAX_SEVEN_DAY_PCT, now):
        return False
    if prov.get("rate_limit_reached"):
        # Only meaningful until the windows it was observed against roll over.
        resets = [_num(w.get("reset_epoch")) for w in (five, weekly)]
        if any(r and r > now for r in resets):
            return False
    return True


def cursor_quota_ok(prov: dict[str, Any], now: float) -> bool:
    """Gate on the pool worker.sh actually draws from (Auto vs named model)."""
    inc = prov.get("included") or {}
    model = os.environ.get("ADVANCE_ROADMAP_CURSOR_WORKER_MODEL", "auto")
    pct = inc.get("auto_pct") if model == "auto" else inc.get("api_pct")
    return not window_exhausted(pct, inc.get("reset_epoch"), MAX_CURSOR_PCT, now)


def recompute_flags(state: dict[str, Any]) -> None:
    now = time.time()
    claude = state["providers"]["claude"]
    ok = claude_available(claude, now) and provider_available_from_limit(claude, now)
    claude["available_for_orchestrator"] = ok
    claude["available_for_worker"] = ok

    codex = state["providers"]["codex"]
    ok = provider_available_from_limit(codex, now) and codex_quota_ok(codex, now)
    codex["available_for_orchestrator"] = ok
    codex["available_for_worker"] = ok

    cursor = state["providers"]["cursor"]
    cursor["available_for_worker"] = (
        provider_available_from_limit(cursor, now) and cursor_quota_ok(cursor, now))


def refresh(root: Path) -> dict[str, Any]:
    state = load(root)
    probe_claude(state)
    probe_codex(state)
    probe_cursor_into(state)
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


def cmd_probe(args: argparse.Namespace) -> None:
    out: dict[str, Any] = {}
    if args.provider in ("codex", "all"):
        out["codex"] = probe_codex_app_server() or probe_codex_session_log()
    if args.provider in ("cursor", "all"):
        out["cursor"] = probe_cursor()
    print(json.dumps(out, indent=2))
    sys.exit(0 if all(out.values()) else 1)


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

    p = sub.add_parser("probe")
    p.add_argument("--provider", default="all", choices=["codex", "cursor", "all"])
    p.set_defaults(func=cmd_probe)

    p = sub.add_parser("show")
    p.set_defaults(func=cmd_show)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
