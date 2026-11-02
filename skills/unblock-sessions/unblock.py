#!/usr/bin/env python3
"""Helper for the unblock-sessions skill.

Walks the Claude sessions running in Superset terminal tabs and hands the model
ONE idle/blocked session at a time, with enough transcript context to ask Jake
about it without him opening the tab.

Scope: every online host in the org by default. `--host HOST` (id, id prefix,
name, or "local") narrows `next` / `list` to that one host. Candidates carry
`host` (display name) and `host_id` ("local" for this machine); pass `host_id`
back as `--host` to relay / screen, or as the first part of a wait target.

Subcommands (all print JSON):
  next  [--host HOST] --seen FILE   next unseen candidate, or {"done": true}
  list  [--host HOST] [--seen FILE] every candidate with its key and filter reason (debug)
  skip-forever KEY LABEL
  no-question KEY
  relay WS TERM [--host HOST_ID] (--text TEXT | --keys KEYS)
  screen WS TERM [--host HOST_ID] [--lines N]
  wait  [HOST_ID:]WS:TERM ...  [--timeout SECONDS]
  open  WS TERM [--print]      jump the Superset GUI to that tab (--print: just show the link)

Every candidate carries `link`, a superset:// deep link to its tab. The link is
host-agnostic (workspace ids are unique) and its focusRequestId is a nonce the
app needs to re-focus a tab it already showed, so `open` mints a fresh one.

Transcripts: this machine's sessions are parsed from the local transcript JSONL
(via host.db). A remote host's disk is out of reach, so its sessions go through
`superset agents read --host`, which returns the transcript text the same way
the non-Claude fallback does (no pending_tool_use; blocked tabs get screen_tail).

Read-only toward Superset's host.db (opened with mode=ro). The only writes are
the state files under ~/.claude/state/unblock-sessions/ and `terminals send`.
"""

import argparse
import glob
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import time
import uuid

STATE_DIR = os.path.expanduser("~/.claude/state/unblock-sessions")
SKIP_FOREVER = os.path.join(STATE_DIR, "skip-forever.json")
NO_QUESTION = os.path.join(STATE_DIR, "no-question.json")
PROJECTS_DIR = os.path.expanduser("~/.claude/projects")

CANDIDATE_STATES = {"idle", "blocked"}
TAIL_BYTES = 4 * 1024 * 1024


def out(obj):
    json.dump(obj, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")


def superset(*args, check=True):
    res = subprocess.run(["superset", *args, "--json"], capture_output=True, text=True)
    if check and res.returncode != 0:
        raise SystemExit(f"superset {' '.join(args)} failed: {res.stderr.strip() or res.stdout.strip()}")
    return res


# --- Hosts -------------------------------------------------------------------

class Host:
    """One Superset host. The local machine uses --local (and no flag for
    snapshot); a remote host uses --host <id>."""

    def __init__(self, hid, name, local=False, online=True):
        self.id, self.name, self.local, self.online = hid, name, local, online

    @property
    def key(self):
        return "local" if self.local else self.id

    @property
    def flags(self):
        return ["--local"] if self.local else ["--host", self.id]

    @property
    def snapshot_flags(self):
        return [] if self.local else ["--host", self.id]


def list_hosts():
    rows = json.loads(superset("hosts", "list").stdout)
    # `hosts list` only says "local" while the desktop app serves this machine;
    # once a standalone `superset start` daemon does, it reports "yes" like any
    # remote. `superset status` carries this machine's host id either way.
    st = superset("status", check=False)
    try:
        local_id = json.loads(st.stdout).get("hostId")
    except json.JSONDecodeError:
        local_id = None
    hosts = []
    for r in rows:
        status = r.get("online")
        hosts.append(Host(r["id"], r.get("name") or r["id"],
                          local=(status == "local" or r["id"] == local_id),
                          online=status in ("local", "yes", True)))
    return hosts


def match_host(hosts, selector):
    """Host named by selector: "local", an id, an id prefix, or a name."""
    sel = selector.lower()
    if sel == "local":
        return next((h for h in hosts if h.local), None)
    exact = [h for h in hosts if h.id.lower() == sel or h.name.lower() == sel]
    if exact:
        return exact[0]
    prefix = [h for h in hosts if h.id.lower().startswith(sel)]
    return prefix[0] if len(prefix) == 1 else None


def get_host(selector):
    """Host for relay/screen/wait. None or "local" means this machine."""
    if selector in (None, "local"):
        return Host("local", "local", local=True)
    host = match_host(list_hosts(), selector)
    if not host:
        raise SystemExit(f"unknown host: {selector}")
    return host


def scan_hosts(selector):
    """(hosts to scan, skipped) for `next` / `list`. No selector = every online
    host; offline ones are reported in skipped. A named host must be online."""
    hosts = list_hosts()
    if selector:
        host = match_host(hosts, selector)
        if not host:
            raise SystemExit(f"unknown host: {selector} (known: {', '.join(h.name for h in hosts)})")
        if not host.online:
            raise SystemExit(f"host {host.name} is offline")
        return [host], []
    return ([h for h in hosts if h.online],
            [{"host": h.name, "reason": "offline"} for h in hosts if not h.online])


def load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path, data):
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def load_seen(path):
    if not path:
        return set()
    try:
        with open(path) as f:
            return {line.strip() for line in f if line.strip()}
    except FileNotFoundError:
        return set()


def trunc(text, limit):
    if text is None:
        return None
    text = text.strip()
    return text if len(text) <= limit else text[:limit] + f"\n…[truncated {len(text) - limit} chars]"


# --- Superset host.db: terminal -> Claude session id -------------------------

def host_db():
    dbs = glob.glob(os.path.expanduser("~/.superset/host/*/host.db"))
    return max(dbs, key=os.path.getmtime) if dbs else None


def session_bindings():
    db = host_db()
    if not db:
        return {}
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "select terminal_id, agent_session_id from terminal_agent_bindings "
            "where agent_session_id is not null"
        ).fetchall()
    finally:
        conn.close()
    return dict(rows)


# --- Transcript parsing ------------------------------------------------------

def transcript_path(session_id):
    hits = glob.glob(os.path.join(PROJECTS_DIR, "*", f"{session_id}.jsonl"))
    return max(hits, key=os.path.getmtime) if hits else None


def parse_lines(lines):
    entries = []
    for line in lines:
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


def read_entries(path):
    """First real user prompt (from the head) plus parsed tail entries."""
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        head = []
        for _ in range(200):
            line = f.readline()
            if not line:
                break
            head.append(line.decode("utf-8", "replace"))
        if size > TAIL_BYTES:
            f.seek(size - TAIL_BYTES)
            f.readline()  # drop the partial line
        else:
            f.seek(0)
        tail = f.read().decode("utf-8", "replace").splitlines()
    return parse_lines(head), parse_lines(tail)


def block_texts(content):
    if isinstance(content, str):
        return [content]
    return [b.get("text", "") for b in content or [] if isinstance(b, dict) and b.get("type") == "text"]


NON_HUMAN_PREFIXES = ("<task-notification>", "<local-command-", "<system-reminder>", "Caveat:")


def human_prompt(entry):
    """Text of a user entry Jake actually typed, else None."""
    if entry.get("type") != "user" or entry.get("isMeta") or entry.get("isCompactSummary"):
        return None
    content = entry.get("message", {}).get("content")
    if isinstance(content, list) and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
        return None
    text = "\n".join(block_texts(content)).strip()
    if not text or text.startswith(NON_HUMAN_PREFIXES):
        return None
    return text


def summarize_transcript(path):
    head, tail = read_entries(path)
    first_prompt = next((t for t in map(human_prompt, head) if t), None)

    last_user_idx = None
    for i, e in enumerate(tail):
        if human_prompt(e):
            last_user_idx = i
    last_user = human_prompt(tail[last_user_idx]) if last_user_idx is not None else None
    turn = tail[last_user_idx + 1:] if last_user_idx is not None else tail

    assistant = [e for e in turn if e.get("type") == "assistant"]
    texts = []
    for e in assistant:
        texts.extend(t for t in block_texts(e.get("message", {}).get("content")) if t.strip())
    final_text = texts[-1] if texts else None
    earlier = "\n\n".join(texts[:-1][-6:]) if len(texts) > 1 else None

    # A tool_use with no matching tool_result is a live dialog (AskUserQuestion,
    # permission prompt) waiting in the tab.
    pending = None
    answered = {
        b.get("tool_use_id")
        for e in turn if e.get("type") == "user"
        for b in (e.get("message", {}).get("content") or [])
        if isinstance(b, dict) and b.get("type") == "tool_result"
    }
    for e in reversed(assistant):
        for b in e.get("message", {}).get("content") or []:
            if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("id") not in answered:
                pending = {"name": b.get("name"), "input": b.get("input")}
                break
        if pending:
            break

    last_uuid = assistant[-1].get("uuid") if assistant else (tail[-1].get("uuid") if tail else None)
    return {
        "first_prompt": trunc(first_prompt, 1500),
        "last_user_message": trunc(last_user, 1500),
        "earlier_turn_text": trunc(earlier, 3000),
        "final_message": trunc(final_text, 6000),
        "pending_tool_use": pending,
        "turn_id": last_uuid,
    }


# --- Candidates --------------------------------------------------------------

def screen_tail(host, ws, term, lines=40):
    res = superset("terminals", "read", *host.flags, "--workspace", ws, "--terminal", term,
                   "--max-lines", str(lines), check=False)
    if res.returncode != 0:
        return None
    try:
        return json.loads(res.stdout).get("text")
    except json.JSONDecodeError:
        return res.stdout


def deep_link(ws, term):
    """superset:// link that focuses a terminal tab in the desktop app."""
    return f"superset://v2-workspace/{ws}?terminalId={term}&focusRequestId={uuid.uuid4()}"


def glyph_of(title):
    if not title:
        return None
    first = title.split(" ", 1)[0]
    return first if not first.isascii() else None


def iter_terminals(hosts, skipped):
    """Yield (host, ws, terminal, agent, reason) across hosts. A host whose
    snapshot fails is appended to `skipped` instead of aborting the run."""
    me = os.environ.get("SUPERSET_TERMINAL_ID")
    for host in hosts:
        res = superset("snapshot", *host.snapshot_flags, check=False)
        if res.returncode != 0:
            skipped.append({"host": host.name, "reason": (res.stderr.strip() or res.stdout.strip())[:200]})
            continue
        for ws in json.loads(res.stdout).get("workspaces", []):
            for t in ws.get("terminals", []):
                agent = t.get("agent") or {}
                reason = None
                if t["id"] == me:
                    reason = "self"
                elif not agent:
                    reason = "no-agent"
                elif agent.get("state") not in CANDIDATE_STATES:
                    reason = f"state:{agent.get('state')}"
                yield host, ws, t, agent, reason


def build_candidate(host, ws, t, agent, bindings):
    # host.db and transcripts are this machine's disk, so only local sessions
    # can be parsed directly; remote ones fall back to `agents read --host`.
    session_id = bindings.get(t["id"]) if host.local else None
    path = transcript_path(session_id) if session_id else None
    cand = {
        "host": host.name,
        "host_id": host.key,
        "workspace_id": ws["id"],
        "workspace_name": ws.get("name"),
        "project": ws.get("project"),
        "branch": ws.get("branch"),
        "terminal_id": t["id"],
        "link": deep_link(ws["id"], t["id"]),
        "title": t.get("title"),
        "glyph": glyph_of(t.get("title")),
        "state": agent.get("state"),
        "agent": agent.get("id"),
        "session_id": session_id,
        "transcript": path,
    }
    if path:
        cand.update(summarize_transcript(path))
    else:
        # Non-Claude agent or unbound tab: fall back to the agent's own reader.
        res = superset("agents", "read", *host.flags, "--workspace", ws["id"], "--terminal", t["id"], check=False)
        text = json.loads(res.stdout).get("text") if res.returncode == 0 else None
        cand["final_message"] = trunc(text[-6000:] if text else None, 6000)
        cand["turn_id"] = hashlib.sha1((text or "").encode()).hexdigest()
    if cand["state"] == "blocked" or cand.get("pending_tool_use"):
        cand["screen_tail"] = screen_tail(host, ws["id"], t["id"])
    cand["key"] = hashlib.sha1(f"{t['id']}|{session_id}|{cand.get('turn_id')}".encode()).hexdigest()[:16]
    return cand


def filter_reason(key, seen, skip_forever, no_question):
    if key in seen:
        return "seen-this-run"
    if key in skip_forever:
        return "skip-forever"
    if key in no_question:
        return "no-question-cached"
    return None


def cmd_next(args):
    seen = load_seen(args.seen)
    skip_forever = load_json(SKIP_FOREVER, {})
    no_question = load_json(NO_QUESTION, {})
    bindings = session_bindings()
    hosts, skipped = scan_hosts(args.host)
    for host, ws, t, agent, reason in iter_terminals(hosts, skipped):
        if reason:
            continue
        cand = build_candidate(host, ws, t, agent, bindings)
        if filter_reason(cand["key"], seen, skip_forever, no_question):
            continue
        out(cand)
        return
    done = {"done": True}
    if skipped:
        done["skipped_hosts"] = skipped
    out(done)


def cmd_list(args):
    seen = load_seen(args.seen)
    skip_forever = load_json(SKIP_FOREVER, {})
    no_question = load_json(NO_QUESTION, {})
    bindings = session_bindings()
    hosts, skipped = scan_hosts(args.host)
    rows = []
    for host, ws, t, agent, reason in iter_terminals(hosts, skipped):
        row = {"host": host.name, "project": ws.get("project"), "workspace": ws.get("name"),
               "terminal_id": t["id"], "title": t.get("title"), "state": agent.get("state")}
        if not reason:
            cand = build_candidate(host, ws, t, agent, bindings)
            row["key"] = cand["key"]
            reason = filter_reason(cand["key"], seen, skip_forever, no_question) or "CANDIDATE"
        row["status"] = reason
        rows.append(row)
    rows.extend({"host": s["host"], "status": f"host-skipped: {s['reason']}"} for s in skipped)
    out(rows)


def cmd_skip_forever(args):
    data = load_json(SKIP_FOREVER, {})
    data[args.key] = {"label": args.label, "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    save_json(SKIP_FOREVER, data)
    out({"ok": True, "skip_forever": args.key})


def cmd_no_question(args):
    data = load_json(NO_QUESTION, {})
    data[args.key] = time.strftime("%Y-%m-%dT%H:%M:%S")
    # Keep the cache bounded; old turns never come back.
    if len(data) > 2000:
        data = dict(sorted(data.items(), key=lambda kv: kv[1])[-1000:])
    save_json(NO_QUESTION, data)
    out({"ok": True, "no_question": args.key})


def cmd_relay(args):
    host = get_host(args.host)
    base = ["terminals", "send", *host.flags, "--workspace", args.workspace, "--terminal", args.terminal]
    if args.keys:
        res = superset(*base, "--keys", args.keys, check=False)
    else:
        res = superset(*base, "--text", args.text, check=False)
    out({"ok": res.returncode == 0, "stdout": res.stdout.strip(), "stderr": res.stderr.strip()})


def cmd_screen(args):
    out({"text": screen_tail(get_host(args.host), args.workspace, args.terminal, args.lines)})


def cmd_open(args):
    url = deep_link(args.workspace, args.terminal)
    if args.print:
        out({"ok": True, "link": url, "opened": False})
        return
    res = subprocess.run(["open", url], capture_output=True, text=True)
    out({"ok": res.returncode == 0, "link": url, "opened": res.returncode == 0,
         "stderr": res.stderr.strip()})


def cmd_wait(args):
    # Target spec: WS:TERM (this machine) or HOST_ID:WS:TERM.
    targets = {}  # terminal id -> host selector
    for spec in args.targets:
        parts = spec.split(":")
        host_sel, term = (parts[0], parts[-1]) if len(parts) == 3 else ("local", parts[-1])
        targets[term] = host_sel
    hosts = {sel: get_host(sel) for sel in set(targets.values())}
    deadline = time.time() + args.timeout
    states = {}
    while True:
        states = {}
        for sel, host in hosts.items():
            res = superset("snapshot", *host.snapshot_flags, check=False)
            if res.returncode != 0:
                continue  # unreachable host: its targets fall through to "gone"
            for ws in json.loads(res.stdout).get("workspaces", []):
                for t in ws.get("terminals", []):
                    if targets.get(t["id"]) == sel:
                        states[t["id"]] = (t.get("agent") or {}).get("state", "exited")
        for term in targets:
            states.setdefault(term, "gone")
        busy = [term for term, s in states.items() if s in ("working", "starting")]
        if not busy or time.time() >= deadline:
            break
        time.sleep(15)
    out({"states": states, "still_working": busy})


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("next")
    s.add_argument("--seen")
    s.add_argument("--host", help="only this host (id, name, or 'local'); default: every online host")
    s.set_defaults(fn=cmd_next)

    s = sub.add_parser("list")
    s.add_argument("--seen")
    s.add_argument("--host", help="only this host (id, name, or 'local'); default: every online host")
    s.set_defaults(fn=cmd_list)

    s = sub.add_parser("skip-forever")
    s.add_argument("key")
    s.add_argument("label")
    s.set_defaults(fn=cmd_skip_forever)

    s = sub.add_parser("no-question")
    s.add_argument("key")
    s.set_defaults(fn=cmd_no_question)

    s = sub.add_parser("relay")
    s.add_argument("workspace")
    s.add_argument("terminal")
    s.add_argument("--host", help="host_id from the candidate; default: this machine")
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--text")
    g.add_argument("--keys")
    s.set_defaults(fn=cmd_relay)

    s = sub.add_parser("screen")
    s.add_argument("workspace")
    s.add_argument("terminal")
    s.add_argument("--lines", type=int, default=40)
    s.add_argument("--host", help="host_id from the candidate; default: this machine")
    s.set_defaults(fn=cmd_screen)

    s = sub.add_parser("open")
    s.add_argument("workspace")
    s.add_argument("terminal")
    s.add_argument("--print", action="store_true", help="print the link without opening it")
    s.set_defaults(fn=cmd_open)

    s = sub.add_parser("wait")
    s.add_argument("targets", nargs="+", help="[HOST_ID:]WORKSPACE_ID:TERMINAL_ID")
    s.add_argument("--timeout", type=int, default=540)
    s.set_defaults(fn=cmd_wait)

    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
