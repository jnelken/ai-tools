#!/usr/bin/env python3
"""Record Codex turn lifecycle events for external status readers.

Logs only lifecycle metadata, never prompts or assistant output. The latest
state for each session is stored separately from the append-only event log.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path


EVENT_STATUS = {
    "UserPromptSubmit": "active",
    "Stop": "idle",
    "Interrupt": "interrupted",
}


def write_event(record: dict[str, object], log_path: Path) -> None:
    log_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(log_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        with os.fdopen(fd, "a", encoding="utf-8", closefd=False) as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            stream.write(json.dumps(record, separators=(",", ":")) + "\n")
            stream.flush()
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
    finally:
        os.close(fd)


def write_state(record: dict[str, object], state_dir: Path) -> None:
    session_id = record.get("session_id")
    if not isinstance(session_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", session_id):
        return

    state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".turn-state-", dir=state_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(record, stream, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temp_name, 0o600)
        os.replace(temp_name, state_dir / f"{session_id}.json")
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def main() -> None:
    try:
        payload = json.load(sys.stdin)
        event = payload.get("hook_event_name")
        status = EVENT_STATUS.get(event)
        if status is None:
            return

        record: dict[str, object] = {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "event": event,
            "status": status,
            "session_id": payload.get("session_id"),
            "turn_id": payload.get("turn_id"),
            "cwd": payload.get("cwd"),
        }
        if event == "Stop":
            record["stop_hook_active"] = bool(payload.get("stop_hook_active", False))

        home = Path.home()
        log_path = Path(os.environ.get("CODEX_TURN_LOG_PATH", home / ".codex" / "turn-activity.jsonl"))
        state_dir = Path(os.environ.get("CODEX_TURN_STATE_DIR", home / ".codex" / "turn-state"))
        write_event(record, log_path)
        write_state(record, state_dir)
    except Exception:
        # Logging must never block or alter a Codex turn.
        return


if __name__ == "__main__":
    main()
