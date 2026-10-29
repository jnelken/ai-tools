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

SECRET_NAME = (r"(?=[A-Z0-9_.-]*(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD|PWD|CREDENTIAL|AUTH|PRIVATE|SESSION|COOKIE))"
               r"[A-Z_][A-Z0-9_.-]*")
SECRET_ASSIGNMENT = re.compile(
    rf"(?i)\b(?P<key>{SECRET_NAME})\s*(?:=|:)\s*(?:"
    r'"(?:\\.|[^"\\])*(?:"|\Z)|'
    r"'(?:\\.|[^'\\])*(?:'|\Z)|"
    r"[^\s,;'\"`]+)"
)
SECRET_FIELD_RE = re.compile(r"(?i)(?:key|token|secret|password|passwd|pwd|credential|auth|private|session|cookie)")
SECRET_PATTERNS = [
    (re.compile(r"(?is)-----BEGIN [^\r\n]*PRIVATE KEY-----.*?(?:-----END [^\r\n]*PRIVATE KEY-----|\Z)"),
     "[REDACTED PRIVATE KEY]"),
    (re.compile(r'(?i)(\bAuthorization\s*:\s*)(?:[A-Za-z][A-Za-z0-9_-]*\s+)?(?:"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|[^\s,;\'"`]+)'),
     r"\1[REDACTED]"),
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+"), "Bearer [REDACTED]"),
    (re.compile(r"(?i)\bBasic\s+[A-Za-z0-9+/=_-]+"), "Basic [REDACTED]"),
    (re.compile(r"\b(?:sk-[A-Za-z0-9_-]{8,}|gh[pousr]_[A-Za-z0-9_]{8,}|github_pat_[A-Za-z0-9_]{8,}|xox[abpr]-[A-Za-z0-9-]{8,})\b"),
     "[REDACTED TOKEN]"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[REDACTED AWS ACCESS KEY]"),
    (re.compile(r"(?i)(?<![\w-])(?:-u|--user)\s+(?:\"(?:\\.|[^\"])*\"|'(?:\\.|[^'])*'|[^\s]+)"),
     "-u [REDACTED]"),
    (re.compile(r"(?i)\b(https?://)[^\s/@:]+:[^\s/@]+@"), r"\1[REDACTED]@"),
]
TICKET_RE = re.compile(r"\b[A-Z][A-Z0-9]{1,9}-\d+\b")
FILE_RE = re.compile(r"(?<![\w.-])((?:\.{1,2}/)?[\w.@+-]+(?:/[\w.@+-]+)*\.(?:md|py|sh|json|jsonl|ya?ml|toml|ini|txt|js|ts|tsx|jsx|css|html|sql|rb|go|rs|c|h|swift|kt|java|xml|plist))\b", re.I)


def redact(text):
    text = str(text)
    for pattern, replacement in SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return SECRET_ASSIGNMENT.sub(lambda match: f"{match.group('key')}=[REDACTED]", text)


def redact_tree(value):
    """Redact JSON leaves before parsing tool inputs into display text or metadata."""
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            clean_key = redact(key) if isinstance(key, str) else key
            result[clean_key] = "[REDACTED]" if isinstance(key, str) and SECRET_FIELD_RE.search(key) else redact_tree(item)
        return result
    if isinstance(value, list):
        return [redact_tree(item) for item in value]
    if isinstance(value, str):
        if value.lstrip().startswith(("{", "[")):
            try:
                nested = json.loads(value)
            except ValueError:
                pass
            else:
                return json.dumps(redact_tree(nested), sort_keys=True)
        return redact(value)
    return value


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
                    records.append(redact_tree(row))
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
                    records.append(redact_tree(row))
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
            args = redact_tree(args)
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


def codex_session_id(session_file="", stdout_file=""):
    if session_file:
        try:
            sid = Path(session_file).read_text(encoding="utf-8", errors="replace").strip().splitlines()[0]
            if re.fullmatch(r"[A-Za-z0-9_-]+", sid):
                return sid
        except (OSError, IndexError):
            pass
    if stdout_file:
        try:
            text = Path(stdout_file).read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        match = re.search(r"^session id: ([A-Za-z0-9_-]+)\s*$", text, re.M | re.I)
        if match:
            return match.group(1)
    return None


def transcript_for(provider, result_json="", session_file="", stdout_file=""):
    sid = (session_id(result_json) if provider == "claude" and result_json
           else codex_session_id(session_file, stdout_file) if provider == "codex" else None)
    if not sid:
        return None, None
    if provider == "claude":
        matches = glob.glob(os.path.join(CLAUDE_PROJECTS, "*", sid + ".jsonl"))
    elif provider == "codex":
        matches = glob.glob(os.path.join(CODEX_SESSIONS, "**", f"rollout-*{sid}.jsonl"), recursive=True)
    else:
        return None, None
    return (sorted(matches)[0], sid) if matches else (None, sid)


def truncate_utf8(text, limit, suffix="\n[Content truncated.]\n"):
    raw = text.encode("utf-8")
    if len(raw) <= limit:
        return text
    suffix_bytes = suffix.encode("utf-8")
    raw = raw[:max(0, limit - len(suffix_bytes))]
    while True:
        try:
            return raw.decode("utf-8") + suffix
        except UnicodeDecodeError:
            raw = raw[:-1]


def encode_bounded(text):
    return truncate_utf8(text, MAX_BYTES, "\n\n[Digest truncated at 20 KB.]\n")


def capture(args):
    if not SAFE_REPO.fullmatch(args.repo):
        return 2
    source, sid = transcript_for(args.provider, getattr(args, "result_json", ""),
                                 getattr(args, "session_file", ""), getattr(args, "stdout", ""))
    records = (claude_records(source) if args.provider == "claude" else codex_records(source)) if source else []
    commands, reads, outputs, interim, files, tickets = (summarize_claude(records) if args.provider == "claude"
                                                          else summarize_codex(records))
    lines = [f"# Partial triage: {args.repo}", "", f"- Stamp: {args.stamp}", f"- Repo: {args.repo}",
             f"- Provider: {args.provider}", f"- Ended: {args.ended}", "",
             f"- Session ID: {sid or 'unavailable'}", f"- Started: {getattr(args, 'started', '') or 'unknown'}", "",
             f"Source: `{os.path.basename(source)}`" if source else "Source: no transcript attributable", ""]
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


def combine(args):
    if not SAFE_REPO.fullmatch(args.repo):
        return 2
    attempts = []
    for path in sorted(Path(args.attempt_dir).glob("partial-triage-attempt-*.md")):
        text = path.read_text(encoding="utf-8", errors="replace")
        provider = re.search(r"^- Provider: (.+)$", text, re.M)
        ended = re.search(r"^- Ended: (.+)$", text, re.M)
        session = re.search(r"^- Session ID: (.+)$", text, re.M)
        started = re.search(r"^- Started: (.+)$", text, re.M)
        lines = text.splitlines()
        try:
            source_line = next(i for i, line in enumerate(lines) if line.startswith("Source:"))
            body = "\n".join(lines[source_line:]).strip()
        except StopIteration:
            body = "Source: no transcript attributable"
        attempts.append({"provider": provider.group(1) if provider else "unknown",
                         "ended": ended.group(1) if ended else "error",
                         "session": session.group(1) if session else "unavailable",
                         "started": started.group(1) if started else "unknown", "body": body})
    if not attempts:
        return 1

    def has_research(text):
        return bool(re.search(r"^## (?:Commands|Reads|Outputs|Interim assistant text)\s*$", text, re.M)
                    or re.search(r"^- Files: (?!none recorded\s*$).+", text, re.M)
                    or re.search(r"^- Tickets: (?!none recorded\s*$).+", text, re.M))

    path = Path(args.out)
    if not any(has_research(attempt["body"]) for attempt in attempts) and path.exists():
        previous = path.read_text(encoding="utf-8", errors="replace")
        if has_research(previous):
            stat = path.stat()
            notes = [f"- Later attempt {args.stamp} unattributable ({attempt['provider']} ended {attempt['ended']})."
                     for attempt in attempts]
            note = "\n\n## Later attempts\n\n" + "\n".join(notes) + "\n"
            prior_limit = MAX_BYTES - len(note.encode("utf-8"))
            preserved = truncate_utf8(previous, max(0, prior_limit), "\n\n[Earlier digest shortened.]\n")
            path.write_text(preserved.rstrip() + note, encoding="utf-8")
            os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
            return 0

    providers = list(dict.fromkeys(attempt["provider"] for attempt in attempts))
    top = "\n".join([f"# Partial triage: {args.repo}", "", f"- Stamp: {args.stamp}", f"- Repo: {args.repo}",
                      f"- Provider: {', '.join(providers)}", f"- Ended: {args.ended}"])
    headers = ["\n".join([f"## Attempt {index}: {attempt['provider']} (ended {attempt['ended']})",
                           f"- Session ID: {attempt['session']}", f"- Started: {attempt['started']}"])
               for index, attempt in enumerate(attempts, 1)]
    fixed_bytes = len(top.encode("utf-8")) + sum(len(header.encode("utf-8")) for header in headers) + 4 * len(headers)
    attempt_budget = max(0, (MAX_BYTES - fixed_bytes) // len(attempts))
    sections = [header + "\n\n" + truncate_utf8(attempt["body"], attempt_budget,
                                                    "\n[Attempt content truncated.]\n")
                for header, attempt in zip(headers, attempts)]
    output = encode_bounded(redact(top + "\n\n" + "\n\n".join(sections)))
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
    c.add_argument("--session-file", default="")
    c.add_argument("--stdout", default="")
    c.add_argument("--started", default="0")
    c.set_defaults(func=capture)
    co = sub.add_parser("combine")
    co.add_argument("--repo", required=True)
    co.add_argument("--stamp", required=True)
    co.add_argument("--ended", required=True)
    co.add_argument("--attempt-dir", required=True)
    co.add_argument("--out", required=True)
    co.set_defaults(func=combine)
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
