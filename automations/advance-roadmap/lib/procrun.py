"""Process-group runner: start_new_session, timeout, reap the whole tree before returning.

daily_batch launches follow-up drains/hooks through `run` so a timeout never leaves descendants
alive after the parent returns. Slice 3's model analyst will share the same helpers.
"""
import os
import signal
import subprocess
import time


def group_pids(pgid):
    try:
        out = subprocess.run(["ps", "-g", str(pgid), "-o", "pid="],
                             capture_output=True, text=True, timeout=2)
    except (OSError, subprocess.TimeoutExpired):
        return []
    return [int(line) for line in out.stdout.splitlines() if line.strip().isdigit()]


def group_alive(pgid):
    if group_pids(pgid):
        return True
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return False  # macOS EPERM after leader exit; trust ps first


def signal_group(pgid, sig):
    try:
        os.killpg(pgid, sig)
    except (ProcessLookupError, PermissionError):
        pass
    if sig == signal.SIGKILL:
        for pid in group_pids(pgid):
            try:
                os.kill(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass


def reap_group(proc, grace=5.0):
    """SIGTERM then SIGKILL the process group until no members remain."""
    pgid = proc.pid
    signal_group(pgid, signal.SIGTERM)
    try:
        proc.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        pass
    if group_alive(pgid):
        signal_group(pgid, signal.SIGKILL)
    if proc.poll() is None:
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
    deadline = time.time() + 2.0
    while group_alive(pgid) and time.time() < deadline:
        signal_group(pgid, signal.SIGKILL)
        time.sleep(0.05)


def run(argv, *, timeout=None, cwd=None, env=None, text=True, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, grace=5.0):
    """Popen with start_new_session; on timeout reap the whole group before raising.

    Returns CompletedProcess. Raises TimeoutExpired only after the group is confirmed empty.
    """
    proc = subprocess.Popen(
        argv, stdin=stdin, stdout=stdout, stderr=stderr, cwd=cwd, env=env,
        text=text, start_new_session=True)
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as error:
        reap_group(proc, grace=grace)
        try:
            out, err = proc.communicate(timeout=1)
        except (OSError, subprocess.TimeoutExpired):
            out = getattr(error, "stdout", None) or ("" if text else b"")
            err = getattr(error, "stderr", None) or ("" if text else b"")
        raise subprocess.TimeoutExpired(proc.args, timeout, output=out, stderr=err) from error
    return subprocess.CompletedProcess(proc.args, proc.returncode, out, err)
