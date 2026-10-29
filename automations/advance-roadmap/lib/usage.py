#!/usr/bin/env python3
"""Multi-provider usage handoff for advance-roadmap.

Machine-readable source of truth for the next run's routing. MODEL_ROUTER.md
remains policy; this file is what run.sh actually reads.

Usage:
  usage.py refresh [--root DIR]
  usage.py pick-orchestrator [--root DIR]
  usage.py pick-orchestrators [--root DIR]        # every eligible planner, in preference order
  usage.py pick-worker-chain [--root DIR]
  usage.py gate --role R --provider P [--root DIR]  # exit 0 under its gate, 3 over it
  usage.py gates --role R [--root DIR]             # every provider's gate for R, as JSON
  usage.py quota-record --role R [--attempts F] [--hit P=F]... [--primary P] [--root DIR]
                                                   # who is out for R when a lane stops on quota
  usage.py record-limit --provider P --text T [--scope S] [--out FILE] [--root DIR]
  usage.py parse-limit --provider P --text T [--scope S]  # the hit as JSON; writes no state
  usage.py live-limit [--transcript-file F] [--exit N] [--final-file F] [--result-json F]
                                                   # live CLI limit only (not an echoed banner)
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
import contextlib
import fcntl
import importlib.util
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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

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



def _pct_env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


# One gate per provider, applied to every role that calls a model (planner, worker, reviewer,
# plan approver): stop routing to a provider well before its hard limit, not at it.
MAX_FIVE_HOUR_PCT = _pct_env("ADVANCE_ROADMAP_MAX_FIVE_HOUR_PCT", 70)
MAX_SEVEN_DAY_PCT = _pct_env("ADVANCE_ROADMAP_MAX_WEEKLY_PCT", 80)
# Cursor quotas are monthly and worker.sh fails over within the tick, so gate
# only when the pool is fully spent (Jake, 2026-10-19: use Cursor to 100%).
MAX_CURSOR_PCT = _pct_env("ADVANCE_ROADMAP_MAX_CURSOR_PCT", 100)

# `gate`'s exit code for "over its gate". Not 1 (a crash) or 2 (bad arguments): callers skip a
# provider only on this code, so a broken usage.py never silently skips every provider.
GATED_EXIT = 3

# Which providers may fill each role, in preference order.
ROLE_PROVIDERS = {
    "orchestrator": ("codex", "claude"),
    "approver": ("codex", "claude"),
    "worker": ("cursor", "codex", "claude"),
    "reviewer": ("codex", "cursor", "claude"),
}
# Cursor draws on a different pool per role: Auto, or the API pool for a named model.
CURSOR_MODEL_ENV = {"worker": "ADVANCE_ROADMAP_CURSOR_WORKER_MODEL",
                    "reviewer": "ADVANCE_ROADMAP_CURSOR_REVIEW_MODEL"}

# A CLI's own limit banner at the start of a line — never prose that merely mentions limits
# (ORCHESTRATOR.md and old logs say "session limit" too). Same pattern as the rg calls in
# run.sh, worker.sh and review.sh.
LIMIT_BANNER = re.compile(
    r"^(?:you.?ve hit your (?:session |usage )?limit|hit your session limit|rate limit exceeded"
    r"|quota exceeded|out of (?:usage|credits|quota)\b)", re.I | re.M)

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
    # Atomic: parallel lanes read this file while others write it.
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path


@contextlib.contextmanager
def state_lock(root: Path):
    """Serialize load-modify-save across processes: a refresh that loaded before a parallel
    lane's record-limit and saved after it would silently drop that limit hit."""
    root.mkdir(parents=True, exist_ok=True)
    with open(root / "providers-usage.lock", "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


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


def windows_gate(windows: list[tuple[str, Any, float]], now: float) -> dict[str, Any]:
    """Gate (label, window, cap) triples: over when any window is at or past its cap.

    A window whose reset has passed reads as 0%. A window with no reading is skipped; when no
    window has one the gate is "unknown", which never blocks but is reported as such.
    """
    over, resets, known = [], [], False
    for label, window, cap in windows:
        w = window if isinstance(window, dict) else {}
        pct = _num(w.get("used_pct"))
        if pct is None:
            continue
        known = True
        if window_exhausted(pct, w.get("reset_epoch"), cap, now):
            over.append(f"{label} {pct:g}% >= {cap:g}%")
            resets.append(_num(w.get("reset_epoch")))
    if over:
        # Usable again only once every window over its cap has reset.
        reset = None if None in resets else max(resets)
        return {"state": "over", "reason": ", ".join(over), "reset_epoch": reset}
    if not known:
        return {"state": "unknown", "reason": "no usage reading", "reset_epoch": None}
    return {"state": "ok", "reason": None, "reset_epoch": None}


def claude_gate(prov: dict[str, Any], now: float) -> dict[str, Any]:
    return windows_gate([("5h", prov.get("five_hour"), MAX_FIVE_HOUR_PCT),
                         ("7d", prov.get("seven_day"), MAX_SEVEN_DAY_PCT)], now)


def codex_gate(prov: dict[str, Any], now: float) -> dict[str, Any]:
    five = prov.get("five_hour") or {}
    weekly = prov.get("weekly") or {}
    gate = windows_gate([("5h", five, MAX_FIVE_HOUR_PCT), ("weekly", weekly, MAX_SEVEN_DAY_PCT)], now)
    if gate["state"] != "over" and prov.get("rate_limit_reached"):
        # Holds only until the next window rolls over; a still-full weekly
        # window is caught by its threshold above. Waiting for every reset
        # would gate Codex for days while no fresh reading can arrive.
        resets = [r for r in (_num(w.get("reset_epoch")) for w in (five, weekly)) if r]
        if resets and min(resets) > now:
            return {"state": "over", "reason": f"rate limit reached ({prov['rate_limit_reached']})",
                    "reset_epoch": min(resets)}
    return gate


def cursor_gate(prov: dict[str, Any], now: float, role: str = "worker") -> dict[str, Any]:
    """Gate on the pool this role actually draws from (Auto vs a named model's API pool)."""
    inc = prov.get("included") or {}
    model = os.environ.get(CURSOR_MODEL_ENV.get(role, CURSOR_MODEL_ENV["worker"]), "auto")
    pool = "auto" if model == "auto" else "api"
    return windows_gate([(f"{pool} pool", {"used_pct": inc.get(f"{pool}_pct"),
                                           "reset_epoch": inc.get("reset_epoch")}, MAX_CURSOR_PCT)], now)


def limit_gate(prov: dict[str, Any], now: float) -> dict[str, Any] | None:
    if provider_available_from_limit(prov, now):
        return None
    reset = _num((prov.get("last_limit_hit") or {}).get("reset_epoch"))
    when = epoch_to_iso(reset)
    return {"state": "limited", "reason": f"limit hit, resets {when}" if when else "limit hit within the last hour",
            "reset_epoch": reset}


def provider_gate(state: dict[str, Any], name: str, role: str, now: float | None = None) -> dict[str, Any]:
    """{'available', 'state', 'reason', 'reset_epoch'} for one provider in one role. state is
    ok | unknown (no reading: available) | over (past its usage gate) | limited (an observed
    limit hit); reset_epoch is when an over/limited provider frees up, when known."""
    now = time.time() if now is None else now
    if name not in ROLE_PROVIDERS.get(role, ()):
        return {"available": False, "state": "n/a", "reason": f"{name} does not fill the {role} role",
                "reset_epoch": None}
    prov = state["providers"][name]
    gate = limit_gate(prov, now)
    if gate is None:
        if name == "cursor":
            gate = cursor_gate(prov, now, role)
        elif name == "codex":
            gate = codex_gate(prov, now)
        else:
            gate = claude_gate(prov, now)
    return {"available": gate["state"] in ("ok", "unknown"), **gate}


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


# A Claude reading older than this says nothing about the window now: it is stored as unknown (no
# percentage), never as the 0% an expired statusline file used to read as.
CLAUDE_READING_MAX_AGE_S = 15 * 60


def claude_quota_lib() -> Path:
    """The ai-tools lib/ that holds claude_quota.py: this file's own checkout (found by walking up
    from its resolved location, so the deploy clone's symlinked copy resolves too), else AI_TOOLS_HOME."""
    here = Path(__file__).resolve().parent
    for parent in (here, *here.parents):
        if (parent / "lib" / "claude_quota.py").is_file():
            return parent / "lib"
    return Path(os.environ.get("AI_TOOLS_HOME", HOME / ".ai-tools")) / "lib"


def _epoch(value: Any) -> float | None:
    """Epoch seconds from a number or an ISO-8601 string (the OAuth usage endpoint sends the latter)."""
    n = _num(value)
    if n is not None:
        return n
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def live_claude_reading() -> dict[str, Any] | None:
    """claude_quota.quota(): the OAuth usage endpoint, falling back inside it to the statusline cache.
    None when probes are off, the module cannot be loaded or it has no windows."""
    if not probes_enabled():
        return None
    try:
        spec = importlib.util.spec_from_file_location("claude_quota", claude_quota_lib() / "claude_quota.py")
        module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        spec.loader.exec_module(module)  # type: ignore[union-attr]
        quota = module.quota()
    except Exception:  # noqa: BLE001 — a broken probe must not break routing
        return None
    windows = quota.get("windows") if isinstance(quota, dict) else None
    observed = _num(quota.get("observed_at")) if isinstance(quota, dict) else None
    if not isinstance(windows, dict) or not windows or observed is None:
        return None
    return {"observed_at": observed, "source": "probe",
            "windows": {key: {"used_pct": (windows.get(key) or {}).get("used_percentage"),
                              "reset_epoch": _epoch((windows.get(key) or {}).get("resets_at"))}
                        for key in ("five_hour", "seven_day")}}


def statusline_claude_reading() -> dict[str, Any] | None:
    """The statusline cache file, observed at its own timestamp (the file's mtime without one)."""
    if not CLAUDE_USAGE.is_file():
        return None
    try:
        raw = json.loads(CLAUDE_USAGE.read_text(encoding="utf-8"))
        observed = _num(raw.get("timestamp")) or CLAUDE_USAGE.stat().st_mtime
    except (OSError, json.JSONDecodeError, AttributeError):
        return None
    windows = {}
    for key in ("five_hour", "seven_day"):
        block = raw.get(key) or {}
        windows[key] = {"used_pct": block.get("used_percentage"), "reset_epoch": _num(block.get("reset_epoch")),
                        "reset_at": block.get("reset_at")}
    return {"observed_at": observed, "source": "statusline", "windows": windows}


def probe_claude(state: dict[str, Any]) -> None:
    prov = state["providers"]["claude"]
    now = time.time()
    # OAuth failures can retain old cached windows; compare observation times rather than
    # letting any cached live result suppress a newer statusline reading.
    readings = [r for r in (live_claude_reading(), statusline_claude_reading()) if r is not None]
    reading = max(readings, key=lambda r: r["observed_at"]) if readings else None
    if reading is None:
        # No source at all. A stored reading we took ourselves survives only while it is still recent;
        # one with no observation time (a hand-written state, an older usage.py) is left alone.
        stored = _num(prov.get("observed_epoch"))
        if stored is None or now - stored <= CLAUDE_READING_MAX_AGE_S:
            return
        reading = {"observed_at": stored, "source": "unavailable",
                   "windows": {k: prov.get(k) or {} for k in ("five_hour", "seven_day")}}
    fresh = reading["observed_at"] is not None and now - reading["observed_at"] <= CLAUDE_READING_MAX_AGE_S
    for key in ("five_hour", "seven_day"):
        block = reading["windows"].get(key) or {}
        reset_epoch = block.get("reset_epoch")
        used = block.get("used_pct") if fresh else None
        if used is not None and reset_epoch and float(reset_epoch) < now:
            used = 0  # that window has rolled over since the reading
        prov[key] = {
            "used_pct": used,
            "reset_at": block.get("reset_at") or epoch_to_iso(reset_epoch),
            "reset_epoch": reset_epoch,
            "source": reading["source"] if fresh else f'{reading["source"]}-stale',
        }
    prov["observed_epoch"] = reading["observed_at"]


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

    Only the subscription-quota percentages are read. The dollar fields
    (includedSpend/limit, displayMessage "You've hit your usage limit") describe
    the separate extra-usage budget, which is set to $0 on purpose and never
    funds these runs, so they are ignored.
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
        "auto_pct", "api_pct", "total_pct", "reset_at", "reset_epoch")}
    inc["source"] = parsed["source"]
    inc["observed_at"] = epoch_to_iso(parsed["observed_epoch"])
    prov["included"] = inc
    prov["source"] = parsed["source"]
    return True


def probe_cursor_into(state: dict[str, Any]) -> None:
    apply_cursor(state, probe_cursor())


def recompute_flags(state: dict[str, Any]) -> None:
    """Stored per-provider flags for the dashboard and the pick-* commands. The gate recorded is
    the worker one; Codex and Claude gate identically in every role."""
    now = time.time()
    for name, prov in state["providers"].items():
        gate = provider_gate(state, name, "worker", now)
        prov["available_for_worker"] = gate["available"]
        prov["gate"] = {k: gate[k] for k in ("state", "reason", "reset_epoch")}
        if name in ROLE_PROVIDERS["orchestrator"]:
            prov["available_for_orchestrator"] = gate["available"]


def refresh(root: Path) -> dict[str, Any]:
    with state_lock(root):
        state = load(root)
        probe_claude(state)
        probe_codex(state)
        probe_cursor_into(state)
        recompute_flags(state)
        save(root, state)
    return state


def pick_orchestrators(state: dict[str, Any]) -> list[str]:
    """Codex Sol, then Claude Sonnet — each only while under its gate."""
    return [n for n in ROLE_PROVIDERS["orchestrator"]
            if state["providers"][n].get("available_for_orchestrator")]


def pick_orchestrator(state: dict[str, Any]) -> str | None:
    """The preferred eligible planner. None if both are over their gates."""
    return next(iter(pick_orchestrators(state)), None)


def pick_worker_chain(state: dict[str, Any]) -> list[str]:
    """Cursor → Codex → Claude, skipping providers over their gates."""
    return [n for n in ROLE_PROVIDERS["worker"] if state["providers"][n].get("available_for_worker")]


_CLOCK_RESET = re.compile(
    r"(?:resets?\s+(?:at\s+)?|try\s+again\s+at\s+)(\d{1,2})(?::(\d{2}))?\s*(?:([ap])\.?\s*m\b\.?)?"
    r"(?:\s*\(\s*([A-Za-z_]+(?:/[A-Za-z0-9_+\-]+)*)\s*\))?", re.I)


def parse_reset_epoch_from_text(text: str, observed: float, tz: Any = None) -> int | None:
    """Reset time from a CLI limit banner, as an epoch.

    Relative ("resets in 2h 30m", "resets in 45m") counts from `observed`. A clock time
    ("resets 11:50am (America/New_York)", "resets 3pm", "try again at 5:00 PM") is
    read in the named zone, else `tz`, else local time; a time already past at `observed`
    means the next day's. A bare hour needs am/pm, so "resets 2026" is not a clock.
    """
    m = re.search(r"resets?\s+in\s+(\d+)\s*h(?:ours?)?\s*(\d+)?\s*m?", text, re.I)
    if m:
        hours = int(m.group(1))
        mins = int(m.group(2) or 0)
        return int(observed + hours * 3600 + mins * 60)
    m = re.search(r"resets?\s+in\s+(\d+)\s*m", text, re.I)
    if m:
        return int(observed + int(m.group(1)) * 60)
    for m in _CLOCK_RESET.finditer(text):
        hour, minute, ampm, zone_name = int(m.group(1)), int(m.group(2) or 0), m.group(3), m.group(4)
        if ampm is None and m.group(2) is None:
            continue
        if ampm:
            if not 1 <= hour <= 12:
                continue
            hour = hour % 12 + (12 if ampm.lower() == "p" else 0)
        if hour > 23 or minute > 59:
            continue
        zone = tz
        if zone_name:
            try:
                zone = ZoneInfo(zone_name)
            except (ZoneInfoNotFoundError, ValueError):
                zone = tz
        today = datetime.fromtimestamp(observed, zone).date()
        later = [t for day in (today, today + timedelta(days=1))
                 for t in _wall_clock_epochs(day, hour, minute, zone) if t > observed]
        return min(later) if later else None
    return None


def _wall_clock_epochs(day: Any, hour: int, minute: int, zone: Any) -> list[int]:
    """Every instant `day hour:minute` names in `zone` (None: local time, with the target date's
    DST rules). Two in the repeated fall-back hour; in the skipped spring-forward hour, the one
    the clock reads after the jump (2:30 → 3:30)."""
    stamps = [int(datetime(day.year, day.month, day.day, hour, minute, fold=fold, tzinfo=zone).timestamp())
              for fold in (0, 1)]
    real = sorted({t for t in stamps if datetime.fromtimestamp(t, zone).timetuple()[3:5] == (hour, minute)})
    return real or [max(stamps)]


def record_limit(
    root: Path,
    provider: str,
    text: str,
    scope: str = "unknown",
) -> dict[str, Any]:
    with state_lock(root):
        return _record_limit_locked(root, provider, text, scope)


def limit_hit(provider: str, text: str, scope: str = "unknown", observed: float | None = None) -> dict[str, Any]:
    """A limit banner as a hit record (provider, the CLI's words, its reset), touching no state —
    so a caller can save what it saw before the best-effort shared-state update."""
    observed = time.time() if observed is None else observed
    reset_epoch = parse_reset_epoch_from_text(text, observed)
    return {
        "observed_at": epoch_to_iso(observed),
        "provider": provider,
        "scope": scope,
        "exact_cli_text": text.strip()[:500],
        "reset_epoch": reset_epoch,
        "reset_at": epoch_to_iso(reset_epoch),
        "reset_source": "derived" if reset_epoch else "unknown",
    }


def _record_limit_locked(root: Path, provider: str, text: str, scope: str) -> dict[str, Any]:
    state = load(root)
    if provider not in state["providers"]:
        raise SystemExit(f"unknown provider: {provider}")
    hit = limit_hit(provider, text, scope)
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


def limit_banner(text: str) -> str | None:
    """The first line that is a CLI's own limit banner, or None."""
    m = LIMIT_BANNER.search(text or "")
    if not m:
        return None
    end = text.find("\n", m.start())
    return text[m.start():end if end >= 0 else len(text)].strip()


def load_provider_result(path: str | Path | None) -> dict | None:
    """Decode a Claude/Cursor --output-format json file, or None.

    The CLI prints one JSON object (a line with a `result` key); stderr warnings may surround it.
    A whole-file json.loads also works when the file is that object alone.
    """
    if not path:
        return None
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except ValueError:
        pass
    for line in reversed(text.splitlines()):
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict) and "result" in obj:
            return obj
    return None


def provider_is_error(result: dict | None) -> bool:
    """True when a Claude/Cursor JSON result is a structured provider error (is_error / error subtype).

    A zero-exit CLI can still fail this way; callers that classify capacity or failure must treat it
    like a non-zero exit, not a successful final answer.
    """
    if not isinstance(result, dict):
        return False
    return bool(result.get("is_error") or result.get("subtype") in ("error", "error_during_execution"))


def provider_reported_limit(result: dict | None) -> str | None:
    """Limit text from a provider's own result/error record, or None.

    Transcripts quote docs and prior logs that contain banner-shaped lines; those must not gate a
    provider. A structured error or `limit_hit` outcome is authoritative.
    """
    if not isinstance(result, dict):
        return None
    if result.get("outcome") == "limit_hit":
        text = " ".join(str(result.get("limit_text") or "").split()) or "provider reported limit_hit"
        return text
    # Claude/Cursor --output-format json: is_error / error subtype carry the CLI's own message.
    if not provider_is_error(result):
        return None
    blob = "\n".join(str(result.get(k) or "") for k in ("result", "error", "error_message", "message"))
    return limit_banner(blob) or (blob.strip().splitlines()[0].strip() if detect_limit(blob) and blob.strip() else None)


def live_limit_banner(
    transcript: str | None = None,
    *,
    exit_code: int | None = None,
    final_answer: str | None = None,
    provider_result: dict | None = None,
) -> str | None:
    """Banner text when this CLI call actually hit a limit; None when a banner is only echoed.

    A limit banner in a transcript counts only when the call failed for that invocation — non-zero
    exit, structured provider error (`is_error`), or no final answer — or when it appears in the
    provider's own error/result record. A successful final answer means ignore banners quoted from
    files, prompts, or prior logs. Zero-exit JSON with `is_error:true` is never a success even when
    `.result` is a generic nonempty string (the live banner may only be on stderr).
    """
    reported = provider_reported_limit(provider_result)
    if reported:
        return reported
    banner = limit_banner(transcript or "")
    if not banner:
        return None
    succeeded = (
        bool((final_answer or "").strip())
        and (exit_code is None or exit_code == 0)
        and not provider_is_error(provider_result)
    )
    if succeeded:
        return None
    # No final answer, non-zero exit, or structured provider error: the banner is a live CLI limit.
    if exit_code is not None and exit_code != 0:
        return banner
    if provider_is_error(provider_result):
        return banner
    if not (final_answer or "").strip():
        return banner
    return None


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
                "gate": p.get("gate"),
            }
            for name, p in state["providers"].items()
        },
    }, indent=2))


def cmd_pick_orchestrators(args: argparse.Namespace) -> None:
    names = pick_orchestrators(refresh(Path(args.root)))
    print(" ".join(names))
    sys.exit(0 if names else 2)


def cmd_gate(args: argparse.Namespace) -> None:
    """Refresh, then exit 0 when the provider may take this role now and GATED_EXIT when it is
    over its gate or limited. Prints the gate as one JSON line (state, reason, reset)."""
    gate = provider_gate(refresh(Path(args.root)), args.provider, args.role)
    print(json.dumps({"provider": args.provider, "role": args.role, **gate,
                      "reset_at": epoch_to_iso(gate.get("reset_epoch"))}))
    sys.exit(0 if gate["available"] else GATED_EXIT)


def role_gates(state: dict[str, Any], role: str) -> list[dict[str, Any]]:
    """Every provider that fills `role`, in preference order, with its gate right now."""
    out = []
    for name in ROLE_PROVIDERS[role]:
        gate = provider_gate(state, name, role)
        out.append({"provider": name, **gate, "reset_at": epoch_to_iso(gate.get("reset_epoch"))})
    return out


OUT_STATES = ("over", "limited")


def quota_record(state: dict[str, Any], role: str, attempts: Any = (), primary: str | None = None,
                 now: float | None = None) -> dict[str, Any]:
    """Who is out for `role` when a lane stops or skips on quota — the one shape every run record
    and the batch summary use.

    `providers` lists every provider in the role's full preference list, not only the ones the lane
    tried: a quota-filtered or explicit chain must not hide a provider that was already gated, or
    its reset. `attempts` are what the lane itself saw ({provider, state, cause/reason, reset_epoch,
    reset_at}): they win over the live gate for that provider, which still fills in a missing reset.
    `provider` is `primary` (the one the lane stopped on) when it is out, else the out provider that
    frees up first; it is None when nobody in the role is out. `cause` is the primary's own words,
    or every out provider's reason joined when there is no primary.
    """
    now = time.time() if now is None else now
    seen = {a["provider"]: a for a in attempts or ()
            if isinstance(a, dict) and a.get("provider") in ROLE_PROVIDERS[role]}
    providers = []
    for name in ROLE_PROVIDERS[role]:
        gate = provider_gate(state, name, role, now)
        entry = {"provider": name, "state": gate["state"], "cause": gate["reason"],
                 "reset_epoch": gate["reset_epoch"], "reset_at": epoch_to_iso(gate["reset_epoch"])}
        a = seen.get(name)
        if a:
            entry["state"] = a.get("state") if a.get("state") in OUT_STATES else "limited"
            entry["cause"] = a.get("cause") or a.get("reason") or entry["cause"]
            if _num(a.get("reset_epoch")) is not None:
                entry["reset_epoch"] = a["reset_epoch"]
                entry["reset_at"] = a.get("reset_at") or epoch_to_iso(a["reset_epoch"])
        providers.append(entry)
    out = [p for p in providers if p["state"] in OUT_STATES]
    first = next((p for p in out if p["provider"] == primary), None)
    if first is None and out:
        resets = [p for p in out if p["reset_epoch"]]
        first = min(resets, key=lambda p: p["reset_epoch"]) if resets else out[0]
    if first is not None and first["provider"] == primary:
        cause = first["cause"]
    else:
        cause = "; ".join(f"{p['provider']}: {p['cause'] or p['state']}" for p in out) or None
    return {"role": role, "provider": first and first["provider"],
            "reset_epoch": first and first["reset_epoch"], "reset_at": first and first["reset_at"],
            "cause": cause, "providers": providers}


def saved_quota_record(root: Path, role: str, attempts: Any = (), primary: str | None = None) -> dict[str, Any]:
    """quota_record() from the gates as last saved, without a refresh: the fallback when
    `quota-record` (which refreshes first) fails, so the observed gates still reach the record."""
    return quota_record(load(Path(root)), role, attempts, primary)


def read_attempts(path: str | None, hits: list[str]) -> list[dict[str, Any]]:
    """--attempts (JSON lines, one per provider) plus --hit P=FILE (a record-limit --out file)."""
    attempts: list[dict[str, Any]] = []
    if path:
        try:
            lines = Path(path).read_text(encoding="utf-8").splitlines()
        except OSError:
            lines = []
        for line in lines:
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if isinstance(entry, dict):
                attempts.append(entry)
    for spec in hits:
        name, _, file = spec.partition("=")
        try:
            hit = json.loads(Path(file).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            hit = {}
        hit = hit if isinstance(hit, dict) else {}
        attempts.append({"provider": name, "state": "limited", "cause": hit.get("exact_cli_text"),
                         "reset_epoch": hit.get("reset_epoch"), "reset_at": hit.get("reset_at")})
    return attempts


def cmd_gates(args: argparse.Namespace) -> None:
    """Refresh, then print {"role", "gates": [...]}: every provider's gate for the role right now."""
    print(json.dumps({"role": args.role, "gates": role_gates(refresh(Path(args.root)), args.role)}))


def cmd_quota_record(args: argparse.Namespace) -> None:
    """Refresh, then print quota_record() as one JSON object."""
    state = refresh(Path(args.root))
    print(json.dumps(quota_record(state, args.role, read_attempts(args.attempts, args.hit), args.primary)))


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
    state = record_limit(Path(args.root), args.provider, args.text, args.scope)
    if args.out:
        Path(args.out).write_text(json.dumps(state["providers"][args.provider]["last_limit_hit"], indent=2) + "\n")
    print("ok")


def cmd_parse_limit(args: argparse.Namespace) -> None:
    print(json.dumps(limit_hit(args.provider, args.text, args.scope)))


def cmd_live_limit(args: argparse.Namespace) -> None:
    """Print the live limit banner (exit 0) or nothing (exit 1) — shared by shell callers."""
    transcript = args.text or ""
    if args.transcript_file:
        transcript = Path(args.transcript_file).read_text(encoding="utf-8", errors="replace")
    final = args.final or ""
    if args.final_file:
        try:
            final = Path(args.final_file).read_text(encoding="utf-8", errors="replace")
        except OSError:
            final = ""
    result = load_provider_result(args.result_json) if args.result_json else None
    banner = live_limit_banner(transcript, exit_code=args.exit, final_answer=final,
                               provider_result=result)
    if not banner:
        sys.exit(1)
    print(banner)


def cmd_is_error(args: argparse.Namespace) -> None:
    """Exit 0 when --result-json is a structured provider error; else 1. For shell capacity checks."""
    sys.exit(0 if provider_is_error(load_provider_result(args.result_json)) else 1)


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

    p = sub.add_parser("pick-orchestrators")
    p.set_defaults(func=cmd_pick_orchestrators)

    p = sub.add_parser("pick-worker-chain")
    p.set_defaults(func=cmd_pick_worker_chain)

    p = sub.add_parser("gate")
    p.add_argument("--role", required=True, choices=sorted(ROLE_PROVIDERS))
    p.add_argument("--provider", required=True, choices=["claude", "codex", "cursor"])
    p.set_defaults(func=cmd_gate)

    p = sub.add_parser("gates")
    p.add_argument("--role", required=True, choices=sorted(ROLE_PROVIDERS))
    p.set_defaults(func=cmd_gates)

    p = sub.add_parser("quota-record")
    p.add_argument("--role", required=True, choices=sorted(ROLE_PROVIDERS))
    p.add_argument("--attempts", help="JSON lines the lane wrote, one per provider it tried or skipped")
    p.add_argument("--hit", action="append", default=[], metavar="PROVIDER=FILE",
                   help="a record-limit --out file for a provider that hit its limit (repeatable)")
    p.add_argument("--primary", help="the provider the lane stopped on")
    p.set_defaults(func=cmd_quota_record)

    p = sub.add_parser("record-limit")
    p.add_argument("--provider", required=True, choices=["claude", "codex", "cursor"])
    p.add_argument("--text", required=True)
    p.add_argument("--scope", default="unknown")
    p.add_argument("--out", help="also write the recorded hit (reset time included) here as JSON")
    p.set_defaults(func=cmd_record_limit)

    p = sub.add_parser("parse-limit")
    p.add_argument("--provider", required=True, choices=["claude", "codex", "cursor"])
    p.add_argument("--text", required=True)
    p.add_argument("--scope", default="unknown")
    p.set_defaults(func=cmd_parse_limit)

    p = sub.add_parser("live-limit")
    p.add_argument("--text", default="", help="transcript text (or use --transcript-file)")
    p.add_argument("--transcript-file")
    p.add_argument("--exit", type=int, dest="exit", default=None,
                   help="CLI exit code; omit when unknown")
    p.add_argument("--final", default="", help="final answer text")
    p.add_argument("--final-file", help="path to the CLI's final-answer file (-o / .last)")
    p.add_argument("--result-json", help="provider result JSON (worker result, Claude/Cursor object)")
    p.set_defaults(func=cmd_live_limit)

    p = sub.add_parser("is-error")
    p.add_argument("--result-json", required=True,
                   help="Claude/Cursor JSON; exit 0 when is_error/error subtype (failed invocation)")
    p.set_defaults(func=cmd_is_error)

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
