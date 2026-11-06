#!/usr/bin/env python3
"""undead: find dead Claude Code sessions and gather evidence of why they died.

Read-only. Stdlib only, so it also runs on a remote host through stdin:
    ssh omen-wsl python3 - autopsy <uuid> < undead.py

Subcommands (all print JSON):
  parse <arg>                 classify a session id / claude.ai link
  locate <uuid>               find the transcript file(s) for a local session UUID
  match --created ISO [--updated ISO] [--title T]
                              find transcripts whose first/last timestamps match a
                              claude.ai session (how a cshk_/session_ id is mapped
                              back to a local UUID; no local file records it)
  scan [--days N]             list recent top-level sessions that look dead
  autopsy <uuid|path>         evidence for why a session died

Options: --projects DIR (repeatable). Default: ~/.claude/projects, plus every
/mnt/c/Users/*/.claude/projects when running inside WSL.
"""
import argparse
import datetime as dt
import glob
import json
import os
import re
import shutil
import subprocess
import sys

UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")

# Text that shows up in a transcript tail when the host died for a known reason.
SIGNALS = [
    ("auth", re.compile(r"authentication_error|OAuth token (has )?expired|Please run /login|invalid x-api-key|401\b.*(auth|token)|not logged in", re.I)),
    ("rate_limit", re.compile(r"rate_limit_error|\b429\b|usage limit|limit reached|resets at", re.I)),
    ("overloaded", re.compile(r"overloaded_error|\b529\b|\b503\b", re.I)),
    ("context", re.compile(r"prompt is too long|context (window|length) exceeded|max_tokens", re.I)),
    ("network", re.compile(r"ECONNRESET|ETIMEDOUT|ENOTFOUND|EAI_AGAIN|socket hang up|fetch failed|Connection error", re.I)),
    ("interrupted", re.compile(r"\[Request interrupted by user", re.I)),
    ("killed", re.compile(r"SIGTERM|SIGKILL|SIGHUP|0xC000013A|terminated", re.I)),
]


def default_projects():
    dirs = [os.path.expanduser("~/.claude/projects")]
    dirs += sorted(glob.glob("/mnt/c/Users/*/.claude/projects"))
    return [d for d in dirs if os.path.isdir(d)]


def parse_ts(s):
    if not s:
        return None
    try:
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def classify(arg):
    arg = arg.strip()
    out = {"input": arg}
    m = re.search(r"cshk_([A-Za-z0-9]+)", arg)
    if m:
        # A claude.ai/code/cshk_ link embeds the API session id: its first 24
        # characters after the prefix are the session_ id.
        out.update(kind="claude_ai_link", bridge_session_id="session_" + m.group(1)[:24])
        return out
    m = re.search(r"session_[A-Za-z0-9]{24}", arg)
    if m:
        out.update(kind="bridge_session_id", bridge_session_id=m.group(0))
        return out
    m = re.search(r"local_(" + UUID_RE.pattern + ")", arg)
    if m:
        out.update(kind="desktop_session_id", uuid=m.group(1))
        return out
    m = UUID_RE.search(arg)
    if m:
        out.update(kind="uuid", uuid=m.group(0))
        return out
    out.update(kind="title_or_unknown")
    return out


def transcripts(projects):
    for d in projects:
        # Top-level transcripts only; subagent sidechains live in <uuid>/subagents/.
        for p in glob.glob(os.path.join(d, "*", "*.jsonl")):
            if UUID_RE.fullmatch(os.path.basename(p)[:-6]):
                yield p


def read_rows(path):
    rows = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                rows.append({"type": "_corrupt", "raw": line[:200]})
    return rows


def first_last_ts(path):
    """Cheap first/last timestamp without parsing the whole file."""
    first = last = None
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            m = re.search(r'"timestamp":"([^"]+)"', line)
            if m:
                first = first or m.group(1)
                last = m.group(1)
    return first, last


def text_of(row):
    msg = row.get("message")
    content = msg.get("content") if isinstance(msg, dict) else row.get("content")
    if isinstance(content, str):
        return content
    parts = []
    for c in content or []:
        if isinstance(c, dict):
            if c.get("type") == "text":
                parts.append(c.get("text", ""))
            elif c.get("type") == "tool_result":
                cc = c.get("content")
                parts.append(cc if isinstance(cc, str) else json.dumps(cc)[:2000])
    return "\n".join(parts)


def live_processes(projects_dir):
    """Map sessionId -> pid for sessions with a running claude process."""
    sessions_dir = os.path.join(os.path.dirname(projects_dir), "sessions")
    live = {}
    windows = projects_dir.startswith("/mnt/")
    win_pids = None
    if windows and shutil.which("tasklist.exe"):
        try:
            out = subprocess.run(["tasklist.exe", "/FO", "CSV", "/NH"], capture_output=True, text=True, timeout=20).stdout
            win_pids = {int(r.split('","')[1]) for r in out.splitlines() if r.count('","') >= 1 and r.split('","')[1].isdigit()}
        except Exception:
            win_pids = None
    for p in glob.glob(os.path.join(sessions_dir, "*.json")):
        try:
            d = json.load(open(p))
            pid = int(d.get("pid"))
        except Exception:
            continue
        if windows:
            alive = None if win_pids is None else pid in win_pids
        else:
            try:
                os.kill(pid, 0)
                alive = True
            except ProcessLookupError:
                alive = False
            except PermissionError:
                alive = True
        if alive is not False:
            live[d.get("sessionId")] = {"pid": pid, "alive": alive, "bridgeSessionId": d.get("bridgeSessionId")}
    return live


def summarize(path, live):
    rows = read_rows(path)
    uuid = os.path.basename(path)[:-6]
    meta = next((r for r in rows if r.get("cwd")), {})
    convo = [r for r in rows if r.get("type") in ("user", "assistant") and not r.get("isSidechain")]
    first_user = next((text_of(r) for r in convo if r.get("type") == "user" and text_of(r).strip()
                       and not text_of(r).lstrip().startswith("<")), "")
    last = convo[-1] if convo else {}
    last_kind = last.get("type")
    if last_kind == "user":
        has_result = isinstance(last.get("message", {}).get("content"), list) and any(
            isinstance(c, dict) and c.get("type") == "tool_result" for c in last["message"]["content"])
        last_kind = "tool_result" if has_result else "user_prompt"
        if last_kind == "user_prompt" and "<command-name>" in text_of(last):
            last_kind = "local_command"  # e.g. /recap, /clear: no reply expected
    api_errors = [{"ts": r.get("timestamp"), "status": r.get("apiErrorStatus"), "text": text_of(r)[:300]}
                  for r in rows if r.get("isApiErrorMessage")]
    # Only harness-authored rows: user prompts and tool output mention "429" or "expired"
    # for unrelated reasons, which made every email-triage transcript look rate limited.
    tail = rows[-40:]
    tail_text = "\n".join(
        [text_of(r) for r in tail if r.get("isApiErrorMessage")]
        + [json.dumps(r)[:400] for r in tail if r.get("type") in ("system", "_corrupt")]
        + [text_of(r)[:80] for r in tail if r.get("type") == "user" and text_of(r).startswith("[Request interrupted")])
    signals = sorted({name for name, rx in SIGNALS if rx.search(tail_text)})
    versions = sorted({r.get("version") for r in rows if r.get("version")})
    first_ts, last_ts = first_last_ts(path)
    proc = live.get(uuid)
    return {
        "uuid": uuid,
        "path": path,
        "cwd": meta.get("cwd"),
        "git_branch": meta.get("gitBranch"),
        "entrypoint": meta.get("entrypoint"),  # sdk-cli = Remote Control bridge or automation; cli = terminal
        "versions": versions,
        "first_ts": first_ts,
        "last_ts": last_ts,
        "first_prompt": first_user[:200],
        "turns": sum(1 for r in convo if r.get("type") == "user"),
        "last_entry": last_kind,  # assistant = turn finished; tool_result/user_prompt = died mid-turn
        "last_text": text_of(last)[:600] if last else "",
        "api_errors": api_errors[-5:],
        "tail_api_error": any(r.get("isApiErrorMessage") for r in tail),
        "signals": signals,
        "live": proc,  # null = no running process holds it
        "corrupt_lines": sum(1 for r in rows if r.get("type") == "_corrupt"),
    }


def mac_power_events(center, minutes=90):
    """Sleep/wake/shutdown events from pmset around the given time (macOS only)."""
    if sys.platform != "darwin" or center is None:
        return None
    try:
        out = subprocess.run(["pmset", "-g", "log"], capture_output=True, text=True, timeout=60).stdout
    except Exception as e:
        return {"error": str(e)}
    lo, hi = center - dt.timedelta(minutes=minutes), center + dt.timedelta(hours=12)
    events, log_starts = [], None
    for line in out.splitlines():
        m = re.match(r"(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d [+-]\d{4})\s+(\S+)\s+(.*)", line)
        if not m:
            continue
        log_starts = log_starts or m.group(1)
        kind, rest = m.group(2), m.group(3)
        # DarkWake, wake requests and maintenance sleeps fire every few minutes overnight
        # and drown out the one transition that matters (the first real sleep / shutdown).
        if kind not in ("Sleep", "Wake", "Shutdown", "Restart", "Start", "Failure"):
            continue
        if rest.startswith("Requests") or re.search(r"Maintenance Sleep|Sleep Service", rest):
            continue
        t = dt.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S %z")
        if lo <= t <= hi:
            events.append(f"{m.group(1)} {kind} {re.sub(r'\s+', ' ', rest)[:140]}")
    covered = bool(log_starts) and dt.datetime.strptime(log_starts, "%Y-%m-%d %H:%M:%S %z") <= lo
    # pmset keeps roughly a week; an empty list outside that window means "no data", not "no sleep".
    return {"log_starts": log_starts, "covers_death": covered, "events": events[:40]}


def later_sessions_same_cwd(info, projects, limit=5):
    """Sessions started in the same cwd after this one died: who replaced it."""
    if not info.get("cwd") or not info.get("last_ts"):
        return []
    died = parse_ts(info["last_ts"])
    found = []
    for p in transcripts(projects):
        if p == info["path"]:
            continue
        first, _ = first_last_ts(p)
        t = parse_ts(first)
        if not t or t < died or t > died + dt.timedelta(days=3):
            continue
        with open(p, encoding="utf-8", errors="replace") as f:
            head = f.read(20000)
        m = re.search(r'"cwd":"([^"]+)"', head)
        e = re.search(r'"entrypoint":"([^"]+)"', head)
        v = re.search(r'"version":"([^"]+)"', head)
        if m and m.group(1) == info["cwd"]:
            found.append({"uuid": os.path.basename(p)[:-6], "first_ts": first,
                          "entrypoint": e and e.group(1), "version": v and v.group(1)})
    return sorted(found, key=lambda x: x["first_ts"])[:limit]


def find_path(arg, projects):
    if os.path.isfile(arg):
        return arg
    hits = [p for p in transcripts(projects) if os.path.basename(p)[:-6] == arg]
    return hits[0] if hits else None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--projects", action="append")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("parse").add_argument("arg")
    sub.add_parser("locate").add_argument("uuid")
    m = sub.add_parser("match")
    m.add_argument("--created", required=True)
    m.add_argument("--updated")
    m.add_argument("--title")
    m.add_argument("--slack-seconds", type=int, default=180)
    s = sub.add_parser("scan")
    s.add_argument("--days", type=int, default=14)
    s.add_argument("--all", action="store_true", help="include sessions whose last turn finished cleanly")
    sub.add_parser("autopsy").add_argument("target")
    a = ap.parse_args()
    projects = a.projects or default_projects()

    if a.cmd == "parse":
        print(json.dumps(classify(a.arg), indent=1))
    elif a.cmd == "locate":
        print(json.dumps([p for p in transcripts(projects) if os.path.basename(p)[:-6] == a.uuid], indent=1))
    elif a.cmd == "match":
        created, updated = parse_ts(a.created), parse_ts(a.updated)
        slack = dt.timedelta(seconds=a.slack_seconds)
        out = []
        for p in transcripts(projects):
            first, last = first_last_ts(p)
            f, l = parse_ts(first), parse_ts(last)
            if not f or abs(f - created) > slack:
                continue
            score = abs((f - created).total_seconds())
            if updated and l:
                score += abs((l - updated).total_seconds())
            hit = {"uuid": os.path.basename(p)[:-6], "path": p, "first_ts": first, "last_ts": last, "score_s": round(score)}
            if a.title:
                with open(p, encoding="utf-8", errors="replace") as fh:
                    hit["title_in_file"] = a.title.lower() in fh.read().lower()
            out.append(hit)
        print(json.dumps(sorted(out, key=lambda h: h["score_s"]), indent=1))
    elif a.cmd == "scan":
        cutoff = dt.datetime.now().timestamp() - a.days * 86400
        out = []
        for d in projects:
            live = live_processes(d)
            for p in transcripts([d]):
                if os.path.getmtime(p) < cutoff:
                    continue
                info = summarize(p, live)
                # One-turn sessions are `claude -p` automations, not threads anyone resumes.
                if info["live"] or info["turns"] <= 1:
                    continue
                died_mid_turn = info["last_entry"] in ("tool_result", "user_prompt")
                bad = died_mid_turn or info["signals"] or info["tail_api_error"]
                if bad or a.all:
                    info["died_mid_turn"] = died_mid_turn
                    for k in ("last_text", "api_errors"):
                        info.pop(k)
                    out.append(info)
        print(json.dumps(sorted(out, key=lambda i: i["last_ts"] or "", reverse=True), indent=1))
    elif a.cmd == "autopsy":
        path = find_path(a.target, projects)
        if not path:
            sys.exit(json.dumps({"error": f"no transcript for {a.target} under {projects}"}))
        live = live_processes(os.path.dirname(os.path.dirname(path)))
        info = summarize(path, live)
        info["power_events"] = mac_power_events(parse_ts(info["last_ts"]))
        info["later_sessions_same_cwd"] = later_sessions_same_cwd(info, projects)
        info["host"] = os.uname().nodename
        info["windows_transcript"] = path.startswith("/mnt/")
        print(json.dumps(info, indent=1))


if __name__ == "__main__":
    main()
