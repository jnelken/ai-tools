#!/usr/bin/env python3
"""Read agent lifecycle metadata; emit a SwiftBar menu or a JSON snapshot."""
from __future__ import annotations

import argparse
import concurrent.futures
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time

HOME = Path.home()
CACHE = HOME / "Library/Caches/ai-tools-agent-count"
CONFIG = HOME / ".config/agent-count/config.json"
WORKING = {"busy", "active", "working", "running"}
WAITING = {"waiting", "blocked", "needs-input", "permission"}


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return default


def run(args):
    return subprocess.check_output(args, text=True, timeout=7, stderr=subprocess.DEVNULL)


def group(provider, states):
    states = list(states)
    return {"provider": provider, "working": sum(s in WORKING for s in states),
            "waiting": sum(s in WAITING for s in states), "total": len(states)}


def snapshot_groups(snapshot, exclude=()):
    providers = {}
    seen = set()
    for workspace in snapshot.get("workspaces", []):
        for terminal in workspace.get("terminals", []):
            agent = terminal.get("agent")
            if not agent or agent.get("id") in exclude:
                continue
            key = (workspace.get("id"), terminal.get("id"))
            if key in seen:
                continue
            seen.add(key)
            providers.setdefault(agent["id"], []).append(agent.get("state", "unknown"))
    return [group(p, states) for p, states in sorted(providers.items())]


def claude_group(processes, home=HOME):
    states = []
    for path in (home / ".claude/sessions").glob("*.json"):
        record = read_json(path, {})
        if not isinstance(record, dict):
            continue
        # Validate against the process table, excluding dead/reused/helper pids.
        pid = str(record.get("pid", path.stem))
        if processes.get(pid) != "claude":
            continue
        states.append(record.get("status", "unknown"))
    return group("claude", states)


def rollout_status(path):
    """Scan backwards for a turn boundary without reading conversation text into output."""
    starts = {"task_started", "turn_started"}
    ends = {"task_complete", "task_completed", "turn_completed", "turn_aborted"}
    try:
        with path.open("rb") as stream:
            position = stream.seek(0, 2)
            remainder = b""
            while position > 0:
                size = min(position, 131072)
                position -= size
                stream.seek(position)
                lines = (stream.read(size) + remainder).split(b"\n")
                remainder = lines.pop(0) if position else b""
                for line in reversed(lines):
                    try:
                        record = json.loads(line)
                    except (ValueError, UnicodeDecodeError):
                        continue
                    if not isinstance(record, dict) or record.get("type") != "event_msg":
                        continue
                    event = (record.get("payload") or {}).get("type")
                    if event in starts:
                        return "active"
                    if event in ends:
                        return "idle"
    except OSError:
        pass
    return None


def codex_group(processes, home=HOME, open_files=None):
    codex_pids = {pid for pid, name in processes.items() if name == "codex"}
    if not codex_pids:
        return group("codex", []), []
    if open_files is None:
        # CLI holds its rollout open. The app-server can expose multiple files.
        try:
            open_files = run(["/usr/sbin/lsof", "-n", "-a", "-c", "codex", "-Fn"])
        except (OSError, subprocess.SubprocessError):
            return group("codex", []), ["Codex session files unavailable; activity unknown"]
    paths, pid = {}, None
    for line in open_files.splitlines():
        if line.startswith("p"):
            pid = line[1:]
        elif pid in codex_pids and line.startswith("n"):
            path = Path(line[1:])
            if path.name.startswith("rollout-") and path.suffix == ".jsonl":
                paths[str(path)] = path
    states = []
    for path in paths.values():
        # A live open rollout ties the hook state to a real session. Read its
        # latest turn boundary first so a missing Stop hook cannot stick busy.
        state = rollout_status(path)
        if state is None:
            session_id = path.stem[-36:]
            record = read_json(home / ".codex/turn-state" / (session_id + ".json"), {})
            state = record.get("status", "unknown") if isinstance(record, dict) else "unknown"
        states.append(state)
    warnings = ["Some Codex sessions have unknown activity"] if "unknown" in states else []
    return group("codex", states), warnings


def collect_local():
    errors = []
    try:
        processes = {}
        for line in run(["/bin/ps", "-axo", "pid=,comm="]).splitlines():
            parts = line.strip().split(None, 1)
            if len(parts) == 2:
                processes[parts[0]] = Path(parts[1]).name
        claude = claude_group(processes)
        codex, errors = codex_group(processes)
        return [claude, codex], errors
    except (OSError, subprocess.SubprocessError):
        return [], ["Local process status unavailable"]


def cli_snapshot(binary, host=None):
    args = [binary, "snapshot", "--json"]
    if host:
        args += ["--host", host]
    result = json.loads(run(args))
    if not isinstance(result, dict) or not isinstance(result.get("workspaces"), list):
        raise ValueError("Invalid host snapshot")
    return result


def merge_local(groups, snapshot):
    # Native registry/process evidence covers agents outside Superset too.
    # Use provider-wise maxima for terminal evidence, never add the same
    # local Claude/Codex again. Terminal evidence fills missing CLI telemetry.
    result = {g["provider"]: dict(g) for g in groups}
    for g in snapshot_groups(snapshot):
        if g["provider"] not in result:
            result[g["provider"]] = g
            continue
        row = result[g["provider"]]
        for key in ("working", "waiting", "total"):
            row[key] = max(row[key], g[key])
        row["total"] = max(row["total"], row["working"] + row["waiting"])
    return sorted(result.values(), key=lambda g: g["provider"])


def remote_hosts(binary):
    """Cache remote metadata for 30s; failures replace counts, never preserve them."""
    cached = read_json(CACHE / "hosts.json", {})
    now = time.time()
    if 0 <= now - cached.get("timestamp", 0) < 30:
        return cached
    result = {"timestamp": now, "hosts": [], "errors": []}
    try:
        hosts = json.loads(run([binary, "hosts", "list", "--json"]))
        if not isinstance(hosts, list):
            raise ValueError("Invalid host list")
        targets = [h for h in hosts if h.get("online") != "local"]
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            futures = {}
            for host in targets:
                row = {"name": host["name"], "id": host["id"], "groups": [],
                       "error": None, "timestamp": now}
                result["hosts"].append(row)
                if str(host.get("online")).lower() not in ("yes", "true", "online"):
                    row["error"] = "Offline; activity unknown"
                else:
                    futures[pool.submit(cli_snapshot, binary, host["id"])] = row
            for future, row in futures.items():
                try:
                    row["groups"] = snapshot_groups(future.result())
                except (OSError, ValueError, subprocess.SubprocessError):
                    row["error"] = "Unreachable; activity unknown"
    except (OSError, ValueError, subprocess.SubprocessError):
        result["errors"].append("Superset host discovery unavailable; remote activity unknown")
    temp = CACHE / ("hosts.%s.tmp" % os.getpid())
    temp.write_text(json.dumps(result))
    temp.replace(CACHE / "hosts.json")
    return result


def collect():
    config = read_json(CONFIG, {})
    binary = config.get("superset") or shutil.which("superset")
    groups, errors = collect_local()
    local = {"name": "This Mac", "groups": groups, "timestamp": time.time(), "error": None}
    hosts = [local]
    if binary:
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            remote = pool.submit(remote_hosts, binary)
            try:
                local["groups"] = merge_local(groups, cli_snapshot(binary))
            except (OSError, ValueError, subprocess.SubprocessError):
                errors.append("Local Superset unavailable; only local Claude/Codex covered")
            data = remote.result()
        hosts.extend(data["hosts"])
        errors.extend(data["errors"])
    else:
        errors.append("Superset CLI missing; only local Claude/Codex covered")
    errors.extend("%s: %s" % (h["name"], h["error"]) for h in hosts if h["error"])
    totals = {key: sum(g[key] for h in hosts for g in h["groups"])
              for key in ("working", "waiting", "total")}
    return dict(totals, hosts=hosts, errors=errors, timestamp=time.time())


def safe(text):
    # Host/provider names are untrusted SwiftBar markup, including submenus.
    return re.sub(r"[\r\n\t|\x00-\x1f]", " ", str(text)).strip().lstrip("-")


def menu(data):
    incomplete = bool(data["errors"])
    count = data["working"]
    warning = " !" if incomplete else ""
    print('%s%s | sfimage=bolt.fill font=Menlo tooltip="%s working agents%s"' %
          (count, warning, count, "; coverage incomplete" if incomplete else ""))
    print("---")
    print("%s working · %s waiting · %s open | size=14" %
          (count, data["waiting"], data["total"]))
    for host in data["hosts"]:
        print("---")
        age = max(0, int(time.time() - host["timestamp"]))
        print("%s · checked %ss ago | size=12" % (safe(host["name"]), age))
        if host["error"]:
            print("%s | sfimage=exclamationmark.triangle" % safe(host["error"]))
        elif not host["groups"]:
            print("No agent sessions")
        for g in host["groups"]:
            print("%s: %s working · %s waiting · %s open" %
                  (safe(g["provider"].capitalize()), g["working"], g["waiting"], g["total"]))
    if data["errors"]:
        print("---")
        for error in data["errors"]:
            print("%s | size=11" % safe(error))
    print("---")
    print("Refresh now | refresh=true")
    print("Open Superset | bash=/usr/bin/open param1=-a param2=Superset terminal=false")
    print("Local: 5s · Superset hosts: 30s | size=11")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    CACHE.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (CACHE / "collector.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            data = collect()
        except Exception:
            data = {"working": 0, "waiting": 0, "total": 0, "hosts": [],
                    "errors": ["Agent status unavailable; retrying in 5 seconds"], "timestamp": time.time()}
    if args.json:
        print(json.dumps(data, indent=2))
    else:
        menu(data)


if __name__ == "__main__":
    main()
