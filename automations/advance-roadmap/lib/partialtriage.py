#!/usr/bin/env python3
"""Build deterministic, bounded partial-triage notes from headless planner transcripts."""
import argparse
import glob
import json
import os
import re
import time
from pathlib import Path

MAX_BYTES = 20 * 1024
OUTPUT_CHARS = 400
AGE_SECONDS = 72 * 60 * 60
SAFE_REPO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
CLAUDE_PROJECTS = os.environ.get("ADVANCE_ROADMAP_CLAUDE_PROJECTS") or os.path.expanduser("~/.claude/projects")
CODEX_SESSIONS = os.environ.get("ADVANCE_ROADMAP_CODEX_SESSIONS") or os.path.expanduser("~/.codex/sessions")

SECRET_PATTERNS = [
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(r"\b([A-Z][A-Z0-9_]{1,})\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s,;]+)"),
    re.compile(r"\b((?:api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|password|passwd))\s*[:=]\s*(\"[^\"]*\"|'[^']*'|[^\s,;]+)", re.I),
]
TICKET_RE = re.compile(r"\b[A-Z][A-Z0-9]{1,9}-\d+\b")
FILE_RE = re.compile(r"(?<![\w.-])((?:\.{1,2}/)?[\w.@+-]+(?:/[\w.@+-]+)*\.(?:md|py|sh|json|jsonl|ya?ml|toml|ini|txt|js|ts|tsx|jsx|css|html|sql|rb|go|rs|c|h|swift|kt|java|xml|plist))\b", re.I)


def redact(text):
    text = str(text)
    text = SECRET_PATTERNS[0].sub("Bearer [REDACTED]", text)
    text = SECRET_PATTERNS[1].sub(r"\1=[REDACTED]", text)
    return SECRET_PATTERNS[2].sub(r"\1=[REDACTED]", text)


def clipped(value, limit=OUTPUT_CHARS):
    text = redact(value).replace("\x00", "")
    return text if len(text) <= limit else text[:limit] + "…[truncated]"


def content_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(b.get("text", "")) for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


def claude_records(path):
    records = []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict) and not row.get("isSidechain"):
                    records.append(row)
    except OSError:
        pass
    return records


def codex_records(path):
    records = []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    records.append(row)
    except OSError:
        pass
    return records


def _claude_tool_blocks(row):
    message = row.get("message") or {}
    content = message.get("content")
    if isinstance(content, list):
        return [b for b in content if isinstance(b, dict)]
    return []


def _path_mentions(text):
    # Collect explicit repository paths without trying to infer arbitrary shell semantics.
    return (set(re.findall(r"(?:^|[\s'\"`])((?:\.?\.?/)?[\w.@+-]+(?:/[\w.@+-]+)+)", text))
            | set(FILE_RE.findall(text)))


def summarize_claude(records):
    commands, reads, outputs, interim, files, tickets = [], [], [], [], set(), set()
    for row in records:
        kind = row.get("type")
        if kind == "assistant":
            for block in _claude_tool_blocks(row):
                if block.get("type") == "tool_use":
                    name, inp = str(block.get("name", "tool")), block.get("input") or {}
                    if name == "Bash":
                        command = inp.get("command", "") if isinstance(inp, dict) else str(inp)
                        commands.append(redact(command))
                        files.update(_path_mentions(command))
                        tickets.update(TICKET_RE.findall(command))
                    elif name in {"Read", "Glob", "Grep", "LS"}:
                        detail = inp.get("file_path") or inp.get("path") or inp.get("pattern") or inp if isinstance(inp, dict) else inp
                        reads.append(f"{name}: {redact(detail)}")
                        if isinstance(detail, str) and name in {"Read", "Grep", "Glob"}:
                            files.add(detail)
                        files.update(_path_mentions(str(detail)))
                        tickets.update(TICKET_RE.findall(str(detail)))
            text = content_text((row.get("message") or {}).get("content"))
            if text.strip():
                interim.append(text.strip())
        elif kind == "user":
            for block in _claude_tool_blocks(row):
                if block.get("type") == "tool_result":
                    outputs.append(clipped(block.get("content", "")))
    alltext = "\n".join(interim + commands + reads + outputs)
    files.update(_path_mentions(alltext))
    tickets.update(TICKET_RE.findall(alltext))
    return commands, reads, outputs, interim, files, tickets


def summarize_codex(records):
    commands, reads, outputs, interim, files, tickets = [], [], [], [], set(), set()
    for row in records:
        payload = row.get("payload") or {}
        typ = payload.get("type") or row.get("type")
        if typ in {"function_call", "tool_call"}:
            name = str(payload.get("name") or payload.get("tool") or "tool")
            raw = payload.get("arguments") or payload.get("input") or ""
            try:
                args = json.loads(raw) if isinstance(raw, str) else raw
            except ValueError:
                args = raw
            if isinstance(args, dict):
                detail = args.get("cmd") or args.get("command") or args.get("file_path") or args.get("path") or json.dumps(args, sort_keys=True)
                for key in ("file_path", "path", "file"):
                    if isinstance(args.get(key), str):
                        files.add(args[key])
            else:
                detail = str(args)
            detail = redact(detail)
            (reads if name.lower() in {"read", "grep", "glob", "list_dir"} else commands).append(f"{name}: {detail}")
            files.update(_path_mentions(detail))
            tickets.update(TICKET_RE.findall(detail))
        elif typ in {"function_call_output", "tool_result"}:
            outputs.append(clipped(payload.get("output") or payload.get("content") or ""))
        elif typ in {"agent_message", "assistant_message"} or (typ == "message" and payload.get("role") == "assistant"):
            msg = payload.get("message") or payload.get("text") or ""
            if not msg and isinstance(payload.get("content"), list):
                msg = "\n".join(str(part.get("text", "")) for part in payload["content"]
                                  if isinstance(part, dict) and part.get("type") in {"output_text", "text"})
            if msg.strip():
                interim.append(msg.strip())
    alltext = "\n".join(interim + commands + reads + outputs)
    files.update(_path_mentions(alltext))
    tickets.update(TICKET_RE.findall(alltext))
    return commands, reads, outputs, interim, files, tickets


def session_id(result_json):
    try:
        lines = Path(result_json).read_text(encoding="utf-8", errors="replace").splitlines()
    except (OSError, ValueError):
        return None
    for line in reversed(lines):
        try:
            data = json.loads(line)
        except ValueError:
            continue
        sid = data.get("session_id") if isinstance(data, dict) else None
        if isinstance(sid, str) and re.fullmatch(r"[A-Za-z0-9_-]+", sid):
            return sid
    return None


def transcript_for(provider, result_json, started):
    sid = session_id(result_json) if result_json else None
    if provider == "claude":
        if sid:
            matches = glob.glob(os.path.join(CLAUDE_PROJECTS, "*", sid + ".jsonl"))
            if matches:
                return max(matches, key=os.path.getmtime)
        pattern = os.path.join(CLAUDE_PROJECTS, "*", "*.jsonl")
    elif provider == "codex":
        if sid:
            matches = glob.glob(os.path.join(CODEX_SESSIONS, "**", f"*{sid}*.jsonl"), recursive=True)
            if matches:
                return max(matches, key=os.path.getmtime)
        pattern = os.path.join(CODEX_SESSIONS, "**", "rollout-*.jsonl")
    else:
        return None
    candidates = [p for p in glob.glob(pattern, recursive=True)
                  if os.path.isfile(p) and os.path.getmtime(p) >= float(started or 0)]
    return max(candidates, key=os.path.getmtime) if candidates else None


def encode_bounded(text):
    raw = text.encode("utf-8")
    if len(raw) <= MAX_BYTES:
        return text
    suffix = "\n\n[Digest truncated at 20 KB.]\n"
    raw = raw[:MAX_BYTES - len(suffix.encode("utf-8"))]
    while True:
        try:
            return raw.decode("utf-8") + suffix
        except UnicodeDecodeError:
            raw = raw[:-1]


def capture(args):
    if not SAFE_REPO.fullmatch(args.repo):
        return 2
    source = transcript_for(args.provider, args.result_json, args.started)
    records = (claude_records(source) if args.provider == "claude" else codex_records(source)) if source else []
    commands, reads, outputs, interim, files, tickets = (summarize_claude(records) if args.provider == "claude"
                                                          else summarize_codex(records))
    lines = [f"# Partial triage: {args.repo}", "", f"- Stamp: {args.stamp}", f"- Repo: {args.repo}",
             f"- Provider: {args.provider}", f"- Ended: {args.ended}", "",
             f"Source: `{os.path.basename(source)}`" if source else "Source: no transcript found", ""]
    if commands:
        lines += ["## Commands", ""] + [f"- `{clipped(x, 1000)}`" for x in commands] + [""]
    if reads:
        lines += ["## Reads", ""] + [f"- {clipped(x, 1000)}" for x in reads] + [""]
    if outputs:
        lines += ["## Outputs", ""] + [f"- {x}" for x in outputs] + [""]
    if interim:
        lines += ["## Interim assistant text", "", clipped("\n\n".join(interim), 12000), ""]
    lines += ["## Files and tickets touched", ""]
    lines += [f"- Files: {', '.join(f'`{redact(x)}`' for x in sorted(files)) or 'none recorded'}",
              f"- Tickets: {', '.join(sorted(tickets, key=str.lower)) or 'none recorded'}", ""]
    output = encode_bounded(redact("\n".join(lines)))
    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(output, encoding="utf-8")
    return 0


def expire(args):
    if not SAFE_REPO.fullmatch(args.repo):
        return 2
    path = Path(args.code_dir) / ".advance-roadmap" / f"partial-triage-{args.repo}.md"
    try:
        if (args.now or time.time()) - path.stat().st_mtime >= AGE_SECONDS:
            path.unlink()
    except FileNotFoundError:
        pass
    return 0


def expire_all(args):
    directory = Path(args.code_dir) / ".advance-roadmap"
    try:
        paths = directory.glob("partial-triage-*.md")
        for path in paths:
            if (args.now or time.time()) - path.stat().st_mtime >= AGE_SECONDS:
                path.unlink()
    except FileNotFoundError:
        pass
    return 0


def prompt(args):
    try:
        text = Path(args.file).read_text(encoding="utf-8")
    except OSError:
        return 1
    stamp = re.search(r"^- Stamp: (.+)$", text, re.M)
    ended = re.search(r"^- Ended: (.+)$", text, re.M)
    if not stamp or not ended:
        return 1
    print(f"Partial triage from {stamp.group(1)} (ended {ended.group(1)}): reuse these findings; "
          f"don't redo those reads unless the fingerprint says they changed. Read: {args.file}")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="command", required=True)
    c = sub.add_parser("capture")
    for name in ("repo", "provider", "ended", "stamp", "out"):
        c.add_argument("--" + name, required=True)
    c.add_argument("--result-json", default="")
    c.add_argument("--started", default="0")
    c.set_defaults(func=capture)
    e = sub.add_parser("expire")
    e.add_argument("--code-dir", required=True)
    e.add_argument("--repo", required=True)
    e.add_argument("--now", type=float, default=0)
    e.set_defaults(func=expire)
    ea = sub.add_parser("expire-all")
    ea.add_argument("--code-dir", required=True)
    ea.add_argument("--now", type=float, default=0)
    ea.set_defaults(func=expire_all)
    p = sub.add_parser("prompt")
    p.add_argument("--file", required=True)
    p.set_defaults(func=prompt)
    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
