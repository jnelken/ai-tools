#!/usr/bin/env python3
"""Durable, body-free state for the Scoutmail skill."""

import argparse
import hashlib
import json
import sqlite3
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


DEFAULT_STATE = Path("/Users/jake/code/scoutmail/.scoutmail/state.sqlite3")


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL CHECK (status IN ('pending', 'successful', 'aborted'))
);

CREATE TABLE IF NOT EXISTS messages (
    message_id TEXT PRIMARY KEY,
    first_seen_at TEXT NOT NULL,
    decision TEXT NOT NULL CHECK (decision IN ('ignore', 'surface')),
    issue_key TEXT
);

CREATE TABLE IF NOT EXISTS issues (
    issue_key TEXT PRIMARY KEY,
    conversation_key TEXT,
    status TEXT NOT NULL CHECK (status IN ('open', 'resolved')),
    summary TEXT NOT NULL,
    action TEXT,
    event_at TEXT,
    last_signature TEXT NOT NULL,
    last_message_id TEXT,
    last_alerted_at TEXT NOT NULL,
    last_reminder_signature TEXT,
    resolved_at TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pending_messages (
    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    message_id TEXT NOT NULL,
    decision TEXT NOT NULL CHECK (decision IN ('ignore', 'surface')),
    issue_key TEXT,
    conversation_key TEXT,
    summary TEXT,
    action TEXT,
    event_at TEXT,
    signature TEXT,
    PRIMARY KEY (run_id, message_id)
);
"""


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0)


def isoformat(value):
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_time(value):
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def emit(payload):
    json.dump(payload, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


def fail(message):
    print("scoutmail-state: {}".format(message), file=sys.stderr)
    raise SystemExit(2)


def connect(path_value):
    path = Path(path_value).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path))
    connection.row_factory = sqlite3.Row
    connection.executescript(SCHEMA)
    return connection


def get_meta(connection, key):
    row = connection.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_meta(connection, key, value):
    connection.execute(
        "INSERT INTO meta(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def issue_rows(connection):
    rows = connection.execute(
        "SELECT issue_key, conversation_key, status, summary, action, event_at, "
        "last_signature, last_message_id, last_alerted_at, last_reminder_signature, "
        "resolved_at, updated_at FROM issues ORDER BY updated_at DESC"
    ).fetchall()
    return [dict(row) for row in rows]


def require_pending_run(connection, run_id):
    row = connection.execute(
        "SELECT run_id, started_at, status FROM runs WHERE run_id = ?", (run_id,)
    ).fetchone()
    if not row:
        fail("unknown run_id {}".format(run_id))
    if row["status"] != "pending":
        fail("run {} is {}, not pending".format(run_id, row["status"]))
    return row


def cmd_begin(args):
    now = parse_time(args.now) if args.now else utc_now()
    if args.enforce_window:
        local_now = now.astimezone(ZoneInfo(args.timezone))
        if local_now.hour < args.start_hour or local_now.hour > args.end_hour:
            emit(
                {
                    "local_time": local_now.replace(microsecond=0).isoformat(),
                    "reason": "outside_run_window",
                    "skip": True,
                    "timezone": args.timezone,
                }
            )
            return
    connection = connect(args.state)
    with connection:
        connection.execute("DELETE FROM pending_messages")
        connection.execute(
            "UPDATE runs SET status = 'aborted', finished_at = ? WHERE status = 'pending'",
            (isoformat(now),),
        )
        last_success = get_meta(connection, "last_successful_at")
        if last_success:
            cursor = parse_time(last_success)
            bootstrap = False
        else:
            cursor = now - timedelta(days=args.bootstrap_days)
            bootstrap = True
        query_after = (cursor - timedelta(days=1)).strftime("%Y/%m/%d")
        run_id = str(uuid.uuid4())
        started_at = isoformat(now)
        connection.execute(
            "INSERT INTO runs(run_id, started_at, status) VALUES(?, ?, 'pending')",
            (run_id, started_at),
        )
    emit(
        {
            "bootstrap": bootstrap,
            "issues": issue_rows(connection),
            "last_successful_at": last_success,
            "query_after": query_after,
            "run_id": run_id,
            "scan_started_at": started_at,
            "state": str(Path(args.state).expanduser()),
        }
    )


def cmd_filter_new(args):
    connection = connect(args.state)
    ordered = list(dict.fromkeys(args.ids))
    if not ordered:
        emit({"seen": [], "unseen": []})
        return
    placeholders = ",".join("?" for _ in ordered)
    rows = connection.execute(
        "SELECT message_id FROM messages WHERE message_id IN ({})".format(placeholders),
        ordered,
    ).fetchall()
    seen_set = {row["message_id"] for row in rows}
    emit(
        {
            "seen": [message_id for message_id in ordered if message_id in seen_set],
            "unseen": [message_id for message_id in ordered if message_id not in seen_set],
        }
    )


def cmd_key(args):
    normalized = " ".join(args.text.casefold().split())
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
    emit({"issue_key": "issue-{}".format(digest), "normalized": normalized})


def cmd_record(args):
    connection = connect(args.state)
    require_pending_run(connection, args.run_id)
    if args.decision == "surface":
        if not args.issue_key or not args.summary or not args.signature:
            fail("surface records require --issue-key, --summary, and --signature")
    with connection:
        connection.execute(
            """
            INSERT INTO pending_messages(
                run_id, message_id, decision, issue_key, conversation_key,
                summary, action, event_at, signature
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id, message_id) DO UPDATE SET
                decision = excluded.decision,
                issue_key = excluded.issue_key,
                conversation_key = excluded.conversation_key,
                summary = excluded.summary,
                action = excluded.action,
                event_at = excluded.event_at,
                signature = excluded.signature
            """,
            (
                args.run_id,
                args.message_id,
                args.decision,
                args.issue_key,
                args.conversation_key,
                args.summary,
                args.action,
                args.event_at,
                args.signature,
            ),
        )
    emit({"message_id": args.message_id, "recorded": True, "run_id": args.run_id})


def cmd_finish(args):
    connection = connect(args.state)
    run = require_pending_run(connection, args.run_id)
    expected = set(args.expected_message_ids)
    pending = connection.execute(
        "SELECT * FROM pending_messages WHERE run_id = ? ORDER BY rowid", (args.run_id,)
    ).fetchall()
    recorded = {row["message_id"] for row in pending}
    if expected != recorded:
        missing = sorted(expected - recorded)
        unexpected = sorted(recorded - expected)
        fail("classification mismatch; missing={}, unexpected={}".format(missing, unexpected))

    finished_at = isoformat(utc_now())
    with connection:
        for row in pending:
            connection.execute(
                "INSERT INTO messages(message_id, first_seen_at, decision, issue_key) "
                "VALUES(?, ?, ?, ?)",
                (row["message_id"], finished_at, row["decision"], row["issue_key"]),
            )
            if row["decision"] == "surface":
                connection.execute(
                    """
                    INSERT INTO issues(
                        issue_key, conversation_key, status, summary, action,
                        event_at, last_signature, last_message_id,
                        last_alerted_at, resolved_at, updated_at
                    ) VALUES(?, ?, 'open', ?, ?, ?, ?, ?, ?, NULL, ?)
                    ON CONFLICT(issue_key) DO UPDATE SET
                        conversation_key = COALESCE(excluded.conversation_key, issues.conversation_key),
                        status = 'open',
                        summary = excluded.summary,
                        action = excluded.action,
                        event_at = excluded.event_at,
                        last_signature = excluded.last_signature,
                        last_message_id = excluded.last_message_id,
                        last_alerted_at = excluded.last_alerted_at,
                        resolved_at = NULL,
                        updated_at = excluded.updated_at
                    """,
                    (
                        row["issue_key"],
                        row["conversation_key"],
                        row["summary"],
                        row["action"],
                        row["event_at"],
                        row["signature"],
                        row["message_id"],
                        finished_at,
                        finished_at,
                    ),
                )
        set_meta(connection, "last_successful_at", run["started_at"])
        set_meta(connection, "accounts", json.dumps(sorted(set(args.accounts))))
        connection.execute(
            "UPDATE runs SET status = 'successful', finished_at = ? WHERE run_id = ?",
            (finished_at, args.run_id),
        )
        connection.execute("DELETE FROM pending_messages WHERE run_id = ?", (args.run_id,))
    emit(
        {
            "accounts": sorted(set(args.accounts)),
            "cursor": run["started_at"],
            "messages_committed": len(pending),
            "run_id": args.run_id,
            "successful": True,
        }
    )


def cmd_abort(args):
    connection = connect(args.state)
    require_pending_run(connection, args.run_id)
    finished_at = isoformat(utc_now())
    with connection:
        connection.execute("DELETE FROM pending_messages WHERE run_id = ?", (args.run_id,))
        connection.execute(
            "UPDATE runs SET status = 'aborted', finished_at = ? WHERE run_id = ?",
            (finished_at, args.run_id),
        )
    emit({"aborted": True, "run_id": args.run_id})


def cmd_issues(args):
    connection = connect(args.state)
    emit({"issues": issue_rows(connection)})


def cmd_resolve(args):
    connection = connect(args.state)
    now = isoformat(utc_now())
    with connection:
        cursor = connection.execute(
            "UPDATE issues SET status = 'resolved', resolved_at = ?, updated_at = ? "
            "WHERE issue_key = ?",
            (now, now, args.issue_key),
        )
    if cursor.rowcount != 1:
        fail("unknown issue_key {}".format(args.issue_key))
    emit({"issue_key": args.issue_key, "resolved": True, "resolved_at": now})


def cmd_remind(args):
    connection = connect(args.state)
    row = connection.execute(
        "SELECT status, last_reminder_signature FROM issues WHERE issue_key = ?",
        (args.issue_key,),
    ).fetchone()
    if not row:
        fail("unknown issue_key {}".format(args.issue_key))
    if row["status"] != "open":
        fail("cannot remind for resolved issue {}".format(args.issue_key))
    if row["last_reminder_signature"] == args.signature:
        emit({"already_recorded": True, "issue_key": args.issue_key, "signature": args.signature})
        return
    now = isoformat(utc_now())
    with connection:
        connection.execute(
            "UPDATE issues SET last_reminder_signature = ?, last_alerted_at = ?, updated_at = ? "
            "WHERE issue_key = ?",
            (args.signature, now, now, args.issue_key),
        )
    emit({"already_recorded": False, "issue_key": args.issue_key, "signature": args.signature})


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", default=str(DEFAULT_STATE), help="SQLite state path")
    commands = parser.add_subparsers(dest="command", required=True)

    begin = commands.add_parser("begin", help="Start a scan without advancing its cursor")
    begin.add_argument("--bootstrap-days", type=int, default=7)
    begin.add_argument("--now", help="Override current time for tests (ISO-8601)")
    begin.add_argument("--enforce-window", action="store_true")
    begin.add_argument("--timezone", default="America/New_York")
    begin.add_argument("--start-hour", type=int, default=7)
    begin.add_argument("--end-hour", type=int, default=22)
    begin.set_defaults(func=cmd_begin)

    filter_new = commands.add_parser("filter-new", help="Split candidate IDs into seen and unseen")
    filter_new.add_argument("--id", dest="ids", action="append", default=[])
    filter_new.set_defaults(func=cmd_filter_new)

    key = commands.add_parser("key", help="Generate a stable issue key")
    key.add_argument("--text", required=True)
    key.set_defaults(func=cmd_key)

    record = commands.add_parser("record", help="Stage one message classification")
    record.add_argument("--run-id", required=True)
    record.add_argument("--message-id", required=True)
    record.add_argument("--decision", choices=("ignore", "surface"), required=True)
    record.add_argument("--issue-key")
    record.add_argument("--conversation-key")
    record.add_argument("--summary")
    record.add_argument("--action")
    record.add_argument("--event-at")
    record.add_argument("--signature")
    record.set_defaults(func=cmd_record)

    finish = commands.add_parser("finish", help="Atomically commit a complete successful scan")
    finish.add_argument("--run-id", required=True)
    finish.add_argument(
        "--expected-message-id", dest="expected_message_ids", action="append", default=[]
    )
    finish.add_argument("--account", dest="accounts", action="append", default=[])
    finish.set_defaults(func=cmd_finish)

    abort = commands.add_parser("abort", help="Abort a scan without advancing its cursor")
    abort.add_argument("--run-id", required=True)
    abort.set_defaults(func=cmd_abort)

    issues = commands.add_parser("issues", help="List known issue-level state")
    issues.set_defaults(func=cmd_issues)

    resolve = commands.add_parser("resolve", help="Resolve an underlying issue")
    resolve.add_argument("--issue-key", required=True)
    resolve.set_defaults(func=cmd_resolve)

    remind = commands.add_parser("remind", help="Record a deadline urgency boundary")
    remind.add_argument("--issue-key", required=True)
    remind.add_argument("--signature", required=True)
    remind.set_defaults(func=cmd_remind)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
