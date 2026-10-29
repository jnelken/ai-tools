#!/usr/bin/env python3
"""Helper for the unblock-sessions skill.

Walks the Claude sessions running in Superset terminal tabs on this host and
hands the model ONE idle/blocked session at a time, with enough transcript
context to ask Jake about it without him opening the tab.

Subcommands (all print JSON):
  next  --seen FILE     next unseen candidate, or {"done": true}
  list  [--seen FILE]   every candidate with its key and filter reason (debug)
  skip-forever KEY LABEL
  no-question KEY
  relay WS TERM (--text TEXT | --keys KEYS)
  screen WS TERM [--lines N]
  wait  WS:TERM [WS:TERM ...] [--timeout SECONDS]

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

def screen_tail(ws, term, lines=40):
    res = superset("terminals", "read", "--local", "--workspace", ws, "--terminal", term,
                   "--max-lines", str(lines), check=False)
    if res.returncode != 0:
        return None
    try:
        return json.loads(res.stdout).get("text")
    except json.JSONDecodeError:
        return res.stdout


def glyph_of(title):
    if not title:
        return None
    first = title.split(" ", 1)[0]
    return first if not first.isascii() else None


def iter_terminals():
    snap = json.loads(superset("snapshot").stdout)
    me = os.environ.get("SUPERSET_TERMINAL_ID")
    for ws in snap.get("workspaces", []):
        for t in ws.get("terminals", []):
            agent = t.get("agent") or {}
            reason = None
            if t["id"] == me:
                reason = "self"
            elif not agent:
                reason = "no-agent"
            elif agent.get("state") not in CANDIDATE_STATES:
                reason = f"state:{agent.get('state')}"
            yield ws, t, agent, reason


def build_candidate(ws, t, agent, bindings):
    session_id = bindings.get(t["id"])
    path = transcript_path(session_id) if session_id else None
    cand = {
        "workspace_id": ws["id"],
        "workspace_name": ws.get("name"),
        "project": ws.get("project"),
        "branch": ws.get("branch"),
        "terminal_id": t["id"],
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
        res = superset("agents", "read", "--local", "--workspace", ws["id"], "--terminal", t["id"], check=False)
        text = json.loads(res.stdout).get("text") if res.returncode == 0 else None
        cand["final_message"] = trunc(text[-6000:] if text else None, 6000)
        cand["turn_id"] = hashlib.sha1((text or "").encode()).hexdigest()
    if cand["state"] == "blocked" or cand.get("pending_tool_use"):
        cand["screen_tail"] = screen_tail(ws["id"], t["id"])
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
    for ws, t, agent, reason in iter_terminals():
        if reason:
            continue
        cand = build_candidate(ws, t, agent, bindings)
        if filter_reason(cand["key"], seen, skip_forever, no_question):
            continue
        out(cand)
        return
    out({"done": True})


def cmd_list(args):
    seen = load_seen(args.seen)
    skip_forever = load_json(SKIP_FOREVER, {})
    no_question = load_json(NO_QUESTION, {})
    bindings = session_bindings()
    rows = []
    for ws, t, agent, reason in iter_terminals():
        row = {"project": ws.get("project"), "workspace": ws.get("name"), "terminal_id": t["id"],
               "title": t.get("title"), "state": agent.get("state")}
        if not reason:
            cand = build_candidate(ws, t, agent, bindings)
            row["key"] = cand["key"]
            reason = filter_reason(cand["key"], seen, skip_forever, no_question) or "CANDIDATE"
        row["status"] = reason
        rows.append(row)
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
    base = ["terminals", "send", "--local", "--workspace", args.workspace, "--terminal", args.terminal]
    if args.keys:
        res = superset(*base, "--keys", args.keys, check=False)
    else:
        res = superset(*base, "--text", args.text, check=False)
    out({"ok": res.returncode == 0, "stdout": res.stdout.strip(), "stderr": res.stderr.strip()})


def cmd_screen(args):
    out({"text": screen_tail(args.workspace, args.terminal, args.lines)})


def cmd_wait(args):
    targets = {}
    for spec in args.targets:
        ws, _, term = spec.partition(":")
        targets[term] = ws
    deadline = time.time() + args.timeout
    states = {}
    while True:
        snap = json.loads(superset("snapshot").stdout)
        states = {}
        for ws in snap.get("workspaces", []):
            for t in ws.get("terminals", []):
                if t["id"] in targets:
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
    s.set_defaults(fn=cmd_next)

    s = sub.add_parser("list")
    s.add_argument("--seen")
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
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--text")
    g.add_argument("--keys")
    s.set_defaults(fn=cmd_relay)

    s = sub.add_parser("screen")
    s.add_argument("workspace")
    s.add_argument("terminal")
    s.add_argument("--lines", type=int, default=40)
    s.set_defaults(fn=cmd_screen)

    s = sub.add_parser("wait")
    s.add_argument("targets", nargs="+", help="WORKSPACE_ID:TERMINAL_ID")
    s.add_argument("--timeout", type=int, default=540)
    s.set_defaults(fn=cmd_wait)

    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
