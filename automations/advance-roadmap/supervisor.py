#!/usr/bin/env python3
"""Run advance-roadmap's sprint windows as parallel lanes — launched by launchd
(com.jake.advance-roadmap) at each window's start; later ticks are a watchdog that exits at
once while a supervisor is alive.

Superset's Production Run board scores width as the median number of agent sessions active
per 15-minute slot, counting only slots with activity. So inside each window this keeps one
lane per available repo busy:

  * a lane is one `run.sh` in lane mode (ADVANCE_ROADMAP_LANE): its own stamp, orchestrator
    pass and worker, in its own Superset workspace, on a repo no other lane holds;
  * lanes start one at a time — each waits until the previous lane has picked its repo, so it
    can be told which repos are busy;
  * a finished lane's slot refills; a lane that finds no work stops new lanes until a running
    lane frees its repo or the world changes (a no-model Linear/repo fingerprint check);
  * no new lane in a window's last CUTOFF minutes; running lanes finish.

Plain Python, no model call: the job has to keep shipping when Claude's usage is spent, and
an LLM coordinator polling all day would spend tokens that ship no PR.

    supervisor.py [--lanes N] [--dry-run]

Windows come from ADVANCE_ROADMAP_WINDOWS ("10:30-15:30 16:00-21:00"). Holds run.lock (with
its pid) while it runs, so a stray lone run.sh skips. Keeps the Mac awake (`caffeinate -i`).
Writes supervisor-state.json and logs/supervisor-*.log.
"""
import argparse
import fcntl
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(os.environ.get("ADVANCE_ROADMAP_ROOT", "/Users/jake/.claude/automations/advance-roadmap"))
HERE = Path(__file__).resolve().parent
RUN_SH = Path(os.environ.get("ADVANCE_ROADMAP_RUN_SH", ROOT / "run.sh"))
LIB = ROOT / "lib"
WINDOWS = os.environ.get("ADVANCE_ROADMAP_WINDOWS", "10:30-15:30 16:00-21:00")
POLL_S = float(os.environ.get("ADVANCE_ROADMAP_SUPERVISOR_POLL_S", "15"))
IDLE_CHECK_MIN = float(os.environ.get("ADVANCE_ROADMAP_IDLE_CHECK_MIN", "15"))
CUTOFF_MIN = float(os.environ.get("ADVANCE_ROADMAP_DISPATCH_CUTOFF_MIN", "30"))
QUOTA_PAUSE_MIN = float(os.environ.get("ADVANCE_ROADMAP_QUOTA_PAUSE_MIN", "30"))
MAX_ERRORS = int(os.environ.get("ADVANCE_ROADMAP_MAX_LANE_ERRORS", "3"))
NEXT_WINDOW_WAIT_MIN = 60  # between windows, stay up when the next one opens this soon
# Outcomes that mean "nothing for this lane to do" — no new lane until something changes.
NO_WORK = {"blocked-no-item", "nothing-qualified", "skipped-unchanged", "archive-only"}
SHIPPED = {"shipped", "shipped-deploy-failed"}
# Outcomes that count toward the consecutive-error breaker (runrecord.ERROR_OUTCOMES minus a red
# deploy, which shipped; plus a lane that died before recording anything).
ERRORS = {"error", "failed", "incomplete", "aborted", "unrecorded"}

LOG = None


def log(msg):
    line = f"{datetime.now():%H:%M:%S} {msg}"
    print(line, flush=True)
    if LOG:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def windows_today(now):
    """[(start, end)] datetimes for today's windows."""
    out = []
    for w in WINDOWS.split():
        a, b = w.split("-")
        start = now.replace(hour=int(a[:2]), minute=int(a[3:5]), second=0, microsecond=0)
        end = now.replace(hour=int(b[:2]), minute=int(b[3:5]), second=0, microsecond=0)
        out.append((start, end))
    return sorted(out)


def current_window(now):
    return next(((s, e) for s, e in windows_today(now) if s <= now < e), None)


def next_window_start(now):
    return next((s for s, _ in windows_today(now) if s > now), None)


def py(script, *args, timeout=300):
    return subprocess.run([sys.executable, str(LIB / script), *args], capture_output=True,
                          text=True, timeout=timeout, stdin=subprocess.DEVNULL)


def read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def run_records(stamps):
    out = {}
    try:
        with open(ROOT / "runs.jsonl", encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("stamp") in stamps:
                    out[r["stamp"]] = r
    except OSError:
        pass
    return out


def code_dir():
    d = os.environ.get("PERSONAL_CODE_DIR")
    if d:
        return d
    try:
        return subprocess.run([str(HERE.parent.parent / "bin" / "personal-code-dir")],
                              capture_output=True, text=True, timeout=10).stdout.strip()
    except OSError:
        return ""


class Lane:
    def __init__(self, n, stamp, proc=None, pid=None, started=None):
        self.n, self.stamp, self.proc = n, stamp, proc
        self.pid = proc.pid if proc else pid
        self.started = started or time.time()
        self.repo = None      # known once the lane's orchestrator (or a reused plan) decides
        self.decided = False
        self.no_work = False  # decided not to dispatch (no work it could take)

    def alive(self):
        if self.proc:
            return self.proc.poll() is None
        try:  # adopted from a supervisor that crashed: not our child, so poll by pid
            os.kill(self.pid, 0)
            return True
        except OSError:
            return False

    def poll_decision(self):
        if self.decided:
            return
        r = read_json(ROOT / "runs" / self.stamp / "orchestrator-result.json")
        if r is not None:
            self.decided = True
            if r.get("action") in ("dispatch_worker", "resume_worker"):
                self.repo = r.get("repo")
            else:
                self.no_work = True
            log(f"lane {self.n} [{self.stamp}] decided: {r.get('action')} {self.repo or ''} "
                f"{r.get('linear_id') or ''}".rstrip())


class Supervisor:
    def __init__(self, a):
        self.a = a
        self.lanes = {}
        self.used_stamps = set()
        self.results = []
        self.queue_empty = False
        self.next_idle_check = 0.0
        self.quota_until = 0.0
        self.consecutive_errors = 0

    def stamp(self):
        while True:
            s = datetime.now().strftime("%Y%m%d-%H%M%S")
            if s not in self.used_stamps and not (ROOT / "runs" / s).exists():
                self.used_stamps.add(s)
                return s
            time.sleep(1.05)

    def busy_repos(self):
        busy = {l.repo for l in self.lanes.values() if l.repo}
        # A worker an earlier run timed out on may still be going in its workspace.
        for f in (ROOT / "inflight").glob("*.pid"):
            try:
                os.kill(int(f.read_text().strip()), 0)
                busy.add(f.name.split("--", 1)[0])
            except (ValueError, OSError):
                pass
        return busy

    def write_state(self, phase):
        shipped = [r for r in self.results if r.get("outcome") in SHIPPED]
        state = {
            "phase": phase,
            "windows": WINDOWS,
            "lanes_max": self.a.lanes,
            "lanes": [{"lane": l.n, "stamp": l.stamp, "repo": l.repo, "pid": l.pid,
                       "started": datetime.fromtimestamp(l.started).isoformat(timespec="seconds")}
                      for l in sorted(self.lanes.values(), key=lambda x: x.n)],
            "finished": len(self.results),
            "shipped": len(shipped),
            "prs": [r.get("pr_url") for r in shipped if r.get("pr_url")],
            "queue_empty": self.queue_empty,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
        tmp = ROOT / f"supervisor-state.json.{os.getpid()}.tmp"
        tmp.write_text(json.dumps(state, indent=1) + "\n")
        os.replace(tmp, ROOT / "supervisor-state.json")

    def superset_up(self):
        if os.environ.get("ADVANCE_ROADMAP_SUPERSET", "1") != "1":
            return True
        return py("workspaces.py", "available", timeout=60).returncode == 0

    def providers_ok(self):
        orch = py("usage.py", "--root", str(ROOT), "pick-orchestrator", timeout=120)
        if orch.returncode != 0 or orch.stdout.strip() in ("", "none"):
            return False, "no orchestrator available"
        work = py("usage.py", "--root", str(ROOT), "pick-worker-chain", timeout=120)
        if work.returncode != 0 or not work.stdout.strip():
            return False, "no worker available"
        return True, ""

    def world_changed(self):
        """No-model check: has anything moved since the last no-work verdict?"""
        snap, fp = ROOT / "supervisor-snapshot.json", ROOT / "supervisor-fingerprint.json"
        py("linearsnap.py", "--out", str(snap), timeout=180)
        py("verdict.py", "fingerprint", "--snapshot", str(snap), "--out", str(fp), timeout=180)
        last = Path(code_dir()) / ".advance-roadmap" / "last-verdict.json"
        chk = py("verdict.py", "check", "--file", str(last), "--fingerprint", str(fp),
                 "--changes", str(ROOT / "supervisor-changes.json"), timeout=60)
        return chk.returncode != 0  # 0 = an unchanged no-work verdict

    def launch(self, n):
        stamp = self.stamp()
        busy = sorted(self.busy_repos())
        inflight = sorted(l.stamp for l in self.lanes.values())
        env = {**os.environ, "ADVANCE_ROADMAP_LANE": str(n), "ADVANCE_ROADMAP_STAMP": stamp,
               "ADVANCE_ROADMAP_BUSY_REPOS": " ".join(busy),
               "ADVANCE_ROADMAP_INFLIGHT_STAMPS": " ".join(inflight)}
        if self.a.dry_run:
            log(f"dry run: would start lane {n} [{stamp}] busy={busy}")
            return
        err = open(ROOT / "logs" / f"lane-{stamp}.err", "w")
        proc = subprocess.Popen(["/bin/zsh", str(RUN_SH)], env=env, stdout=subprocess.DEVNULL,
                                stderr=err, stdin=subprocess.DEVNULL, start_new_session=True)
        self.lanes[n] = Lane(n, stamp, proc)
        log(f"lane {n} [{stamp}] started (busy: {' '.join(busy) or 'none'})")

    def reap(self):
        done = [l for l in self.lanes.values() if not l.alive()]
        if not done:
            return
        recs = run_records({l.stamp for l in done})
        for l in done:
            del self.lanes[l.n]
            r = recs.get(l.stamp) or {"stamp": l.stamp, "outcome": "unrecorded"}
            self.results.append(r)
            outcome = r.get("outcome")
            log(f"lane {l.n} [{l.stamp}] finished in {(time.time() - l.started) / 60:.0f}m: {outcome} "
                f"{r.get('repo') or ''} {r.get('pr_url') or r.get('detail') or ''}".rstrip())
            err = ROOT / "logs" / f"lane-{l.stamp}.err"
            try:
                if err.exists() and err.stat().st_size == 0:
                    err.unlink()
            except OSError:
                pass
            if outcome in NO_WORK:
                self.queue_empty = True
                self.next_idle_check = time.time() + IDLE_CHECK_MIN * 60
            else:
                self.queue_empty = False  # a repo just freed up, and a ship may unblock others
            if outcome == "skipped-quota":
                self.quota_until = time.time() + QUOTA_PAUSE_MIN * 60
            if outcome in ERRORS:
                self.consecutive_errors += 1
            elif outcome not in ("skipped-superset", "skipped-quota", "skipped-lock"):
                self.consecutive_errors = 0

    def can_dispatch(self, now):
        win = current_window(datetime.now())
        if not win:
            return False, "between windows"
        if datetime.now() >= win[1] - timedelta(minutes=CUTOFF_MIN):
            return False, "past this window's dispatch cutoff"
        if len(self.lanes) >= self.a.lanes:
            return False, "all lanes busy"
        if any(not l.decided for l in self.lanes.values()):
            return False, "waiting for a lane to pick its repo"
        if self.consecutive_errors >= MAX_ERRORS:
            return False, f"{self.consecutive_errors} lane errors in a row"
        if now < self.quota_until:
            return False, "provider quota pause"
        if self.queue_empty:
            if self.lanes:
                return False, "queue empty until a lane frees its repo"
            if now < self.next_idle_check:
                return False, "queue empty — idle"
            self.next_idle_check = now + IDLE_CHECK_MIN * 60
            if not self.world_changed():
                return False, "queue empty — nothing changed"
            log("idle check: something changed — dispatching")
            self.queue_empty = False
        return True, ""

    def should_exit(self):
        if self.lanes:
            return False
        if self.consecutive_errors >= MAX_ERRORS:
            return True
        now = datetime.now()
        win = current_window(now)
        if win and now < win[1] - timedelta(minutes=CUTOFF_MIN):
            return False  # still dispatching
        nxt = next_window_start(now)
        return not (nxt and nxt - now <= timedelta(minutes=NEXT_WINDOW_WAIT_MIN))

    def adopt(self):
        """Lanes a crashed supervisor left running: track them so no lane doubles up on their repos."""
        prior = read_json(ROOT / "supervisor-state.json") or {}
        for l in prior.get("lanes") or []:
            lane = Lane(l["lane"], l["stamp"], pid=l.get("pid"),
                        started=datetime.fromisoformat(l["started"]).timestamp())
            if lane.pid and lane.alive():
                lane.repo, lane.decided = l.get("repo"), True
                self.lanes[lane.n] = lane
                self.used_stamps.add(lane.stamp)
                log(f"adopted lane {lane.n} [{lane.stamp}] {lane.repo or ''} from a previous supervisor")

    def run(self):
        log(f"supervisor up: windows {WINDOWS}, up to {self.a.lanes} lanes")
        self.adopt()
        if self.should_exit():
            log("outside the sprint windows — nothing to do")
            return 0
        if os.environ.get("ADVANCE_ROADMAP_SUPERSET", "1") == "1" and self.superset_up():
            out = py("workspaces.py", "cleanup", timeout=600)
            for line in (out.stdout + out.stderr).strip().splitlines():
                log(line)
        last_why = ""
        while True:
            self.reap()
            for l in self.lanes.values():
                l.poll_decision()
                if l.no_work and not self.queue_empty:
                    # Don't fill the other slots with runs that would find the same nothing.
                    self.queue_empty = True
                    self.next_idle_check = time.time() + IDLE_CHECK_MIN * 60
            now = time.time()
            ok, why = self.can_dispatch(now)
            if ok and not self.superset_up():
                ok, why = False, "Superset app or host service not reachable"
            if ok:
                ok, why = self.providers_ok()
                if not ok:
                    self.quota_until = now + QUOTA_PAUSE_MIN * 60
            if ok:
                self.launch(next(n for n in range(1, self.a.lanes + 1) if n not in self.lanes))
                if self.a.dry_run:
                    return 0
                last_why = ""
            elif why != last_why:
                log(f"not dispatching: {why} ({len(self.lanes)} running)")
                last_why = why
            self.write_state("running")
            if self.should_exit():
                break
            time.sleep(POLL_S)
        shipped = [r for r in self.results if r.get("outcome") in SHIPPED]
        self.write_state("done")
        log(f"supervisor done: {len(shipped)} shipped across {len(self.results)} lane run(s)"
            + (f"; stopped after {self.consecutive_errors} lane errors in a row"
               if self.consecutive_errors >= MAX_ERRORS else ""))
        return 0


def take_run_lock():
    """run.lock (a dir holding its holder's pid), the lock a lone run.sh takes. Waits out a live
    lone run for up to an hour."""
    lock = ROOT / "run.lock"
    for _ in range(240):
        live = False
        if lock.is_dir():
            try:
                holder = int((lock / "pid").read_text().strip())
                os.kill(holder, 0)
                live = holder != os.getpid()
            except (OSError, ValueError):
                live = not (lock / "pid").exists() and time.time() - lock.stat().st_mtime < 4 * 3600
        if not live:
            shutil.rmtree(lock, ignore_errors=True)
            lock.mkdir()
            (lock / "pid").write_text(f"{os.getpid()}\n")
            return lock
        time.sleep(15)
    return None


def main(argv=None):
    global LOG
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--lanes", type=int, default=int(os.environ.get("ADVANCE_ROADMAP_LANES", "8")),
                   help="most lanes at once (default 8); in practice, as many as there are repos with work")
    p.add_argument("--dry-run", action="store_true", help="log the first dispatch decision and stop")
    a = p.parse_args(argv)

    marker = os.path.expanduser("~/dotfiles/bin/is-personal-machine")
    if os.path.exists(marker) and subprocess.run([marker]).returncode == 1:
        print("advance-roadmap: work machine — runs on the personal machines only.")
        return 0
    if not code_dir():
        print("advance-roadmap: no personal code dir on this machine — nothing to do.")
        return 0
    if not current_window(datetime.now()):
        nxt = next_window_start(datetime.now())
        if not (nxt and nxt - datetime.now() <= timedelta(minutes=NEXT_WINDOW_WAIT_MIN)):
            return 0  # a catch-up fire on wake, outside the windows: leave no trace
    (ROOT / "logs").mkdir(parents=True, exist_ok=True)
    single = open(ROOT / "supervisor.lock", "w")
    try:
        fcntl.flock(single, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return 0  # the watchdog tick: a supervisor is already running this window
    LOG = ROOT / "logs" / f"supervisor-{datetime.now():%Y%m%d-%H%M%S}.log"
    lock = None if a.dry_run else take_run_lock()
    if not a.dry_run and lock is None:
        log("a lone run held run.lock for an hour — giving up")
        return 1
    caffeinate = None
    if shutil.which("caffeinate") and not a.dry_run:
        # Idle sleep would freeze every lane mid-build (a closed lid still sleeps the Mac).
        caffeinate = subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())])
    try:
        return Supervisor(a).run()
    finally:
        if caffeinate:
            caffeinate.terminate()
        if lock:
            shutil.rmtree(lock, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
