#!/usr/bin/env python3
"""Per-repo directive decisions. All mutations share a locked, atomic write path."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import inspect
import json
import os
from pathlib import Path
import re
import sys
import tempfile

# Isolated Python omits the script directory; only trust our sibling modules.
sys.path.insert(0, str(Path(__file__).resolve().parent))


class StoreError(ValueError):
    """The store could not be read safely."""


def store_dir():
    return Path(os.environ.get("DIRECTIVE_STORE_DIR", "~/.claude/automations/advance-roadmap/directives")).expanduser()


def store_path(repo):
    if not repo or repo in (".", "..") or any(c in repo for c in ("/", "\\", "\0")):
        raise StoreError(f"invalid repo name: {repo!r}")
    return store_dir() / f"{repo}.json"


def has_store(repo):
    return store_path(repo).exists()


def _read(repo):
    path = store_path(repo)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"repo": repo, "decisions": []}
    except (ValueError, UnicodeError) as exc:
        raise StoreError(f"corrupt directive store {path}: {exc}") from exc
    if (not isinstance(data, dict) or data.get("repo") != repo
            or not isinstance(data.get("decisions"), list)
            or any(not isinstance(d, dict) or not isinstance(d.get("id"), str)
                   or d.get("status") not in ("pending", "consumed")
                   for d in data["decisions"])):
        raise StoreError(f"corrupt directive store {path}: invalid repo or decisions")
    return data


@contextmanager
def _locked(repo):
    path = store_path(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield _read(repo)


def _write(repo, data):
    path = store_path(repo)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=f".{repo}.", suffix=".tmp", delete=False) as out:
            temp_path = Path(out.name)
            json.dump(data, out, indent=2, ensure_ascii=False)
            out.write("\n")
            out.flush()
            os.fsync(out.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def _now():
    return datetime.now(timezone.utc).isoformat()


def decision_id(ticket, question):
    return hashlib.sha256((ticket + "\n" + question).encode("utf-8")).hexdigest()[:16]


def upsert(repo, ticket, item, question, answer):
    ident = decision_id(ticket, question)
    with _locked(repo) as data:
        decision = next((d for d in data["decisions"] if d["id"] == ident), None)
        if decision is not None and decision.get("answer") == answer:
            return {"id": ident, "status": "unchanged"}
        status = "updated" if decision is not None else "created"
        if decision is None:
            decision = {"id": ident}
            data["decisions"].append(decision)
        decision.update(ticket=ticket, item=item, question=question, answer=answer,
                        status="pending", recorded_at=_now(), consumed_at=None, consumed_note=None)
        _write(repo, data)
    return {"id": ident, "status": status}


def list_pending(repo):
    return [d for d in _read(repo)["decisions"] if d["status"] == "pending"]


def pending_repos():
    return {path.stem: len(pending) for path in sorted(store_dir().glob("*.json"))
            if (pending := list_pending(path.stem))}


def consume(repo, ids, note):
    ids = set(ids)
    with _locked(repo) as data:
        count = 0
        for decision in data["decisions"]:
            if decision["id"] in ids and decision["status"] == "pending":
                decision.update(status="consumed", consumed_at=_now(), consumed_note=note)
                count += 1
        if count:
            _write(repo, data)
    return count


def parse_snapshot(repo, snapshot):
    """Extract decisions from pending sections, retaining their original ticket identity."""
    import directive_slot

    store_path(repo)  # Validate even if there are no matching sections.
    if not snapshot.get("ok", False):
        raise StoreError(f"Linear snapshot unavailable: {snapshot.get('error', 'unknown')}")
    decisions, skipped = [], []
    for issue in snapshot.get("issues", []):
        description = issue.get("description") or ""
        entries = directive_slot.pending_entries(description)
        for entry_repo, start in entries:
            if entry_repo != repo:
                continue
            ticket = issue.get("identifier") or issue.get("id")
            # Bound the parser to this section, including when another section is consumed.
            next_marker = directive_slot.MARKER_RE.search(description, start + 1)
            section = description[start:next_marker.start() if next_marker else len(description)]
            info, reason = directive_slot.parse_directive(section, 0)
            if reason or not ticket:
                skipped.append(f"{ticket or 'unknown ticket'}: {reason or 'missing ticket identifier'}")
                continue
            bullets = re.split(r"(?m)^[ \t]*[*-][ \t]+(?=\*\*Item:\*\*)", info["answered"])
            if len(bullets) == 1:
                skipped.append(f"{ticket}: no parseable Answered decisions bullets")
            for bullet in bullets[1:]:
                fields = list(re.finditer(r"\*\*(Item|Question|Answer|Recorded in):\*\*", bullet))
                values = {}
                for i, field in enumerate(fields):
                    end = fields[i + 1].start() if i + 1 < len(fields) else len(bullet)
                    values[field.group(1)] = inspect.cleandoc(bullet[field.end():end]).strip()
                if not all(values.get(key) for key in ("Item", "Question", "Answer")):
                    skipped.append(f"{ticket}: incomplete Answered decisions bullet")
                    continue
                decisions.append(dict(ticket=ticket, item=values["Item"],
                                      question=values["Question"], answer=values["Answer"]))
    return decisions, skipped


def import_snapshot(repo, snapshot):
    _read(repo)  # Refuse a corrupt existing store even when nothing can be imported.
    decisions, skipped = parse_snapshot(repo, snapshot)
    summary = dict(created=0, updated=0, unchanged=0, skipped=skipped)
    for decision in decisions:
        summary[upsert(repo, **decision)["status"]] += 1
    return summary


def render_status(repo):
    data = _read(repo)
    pending = [d for d in data["decisions"] if d["status"] == "pending"]
    consumed = len(data["decisions"]) - len(pending)
    lines = [f"## Directives: {repo}", "", f"{len(pending)} pending decision(s); {consumed} consumed."]
    for d in pending:
        lines.extend(["", f"* {d['ticket']} — {d['item']} (`{d['id']}`)",
                      f"  Question: {d['question']}", f"  Answer: {d['answer']}"])
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    up = commands.add_parser("upsert")
    for key in ("repo", "ticket", "item", "question", "answer"):
        up.add_argument(f"--{key}", required=True)
    commands.add_parser("list").add_argument("--repo", required=True)
    con = commands.add_parser("consume")
    con.add_argument("--repo", required=True)
    con.add_argument("--ids", nargs="+", required=True)
    con.add_argument("--note", required=True)
    commands.add_parser("pending-repos")
    imp = commands.add_parser("import")
    imp.add_argument("--repo", required=True)
    imp.add_argument("--snapshot")
    args = parser.parse_args(argv)
    try:
        if args.command == "upsert":
            result = upsert(args.repo, args.ticket, args.item, args.question, args.answer)
        elif args.command == "list":
            result = list_pending(args.repo)
        elif args.command == "consume":
            result = consume(args.repo, args.ids, args.note)
        elif args.command == "pending-repos":
            result = pending_repos()
        else:
            import directive_slot
            store_path(args.repo)
            result = import_snapshot(args.repo, directive_slot.load_snapshot(args.snapshot))
        print(json.dumps(result))
        return 0
    except (OSError, ValueError) as exc:
        print(f"directive_store: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
