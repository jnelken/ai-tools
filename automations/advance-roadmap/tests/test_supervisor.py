#!/usr/bin/env python3
"""supervisor.py end to end with a fake run.sh: lanes run in parallel, never two on one repo,
each lane is told the busy repos, and the supervisor stops at the window's dispatch cutoff."""

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]

FAKE_RUN = r'''#!/bin/zsh
# Pick the first repo no other lane holds; "work" for a few seconds; record the run.
root="$ADVANCE_ROADMAP_ROOT"; stamp="$ADVANCE_ROADMAP_STAMP"
mkdir -p "$root/runs/$stamp"
repo=""
for r in ${=FAKE_REPOS}; do [[ " $ADVANCE_ROADMAP_BUSY_REPOS " == *" $r "* ]] || { repo=$r; break; }; done
if [ -z "$repo" ]; then
  print -r -- '{"action":"blocked_no_item","repo":null}' > "$root/runs/$stamp/orchestrator-result.json"
  sleep "${FAKE_SLEEP:-3}"   # bookkeeping takes a while before the run is recorded
  print -r -- "{\"stamp\":\"$stamp\",\"outcome\":\"blocked-no-item\"}" >> "$root/runs.jsonl"
  exit 0
fi
print -r -- "{\"action\":\"dispatch_worker\",\"repo\":\"$repo\"}" > "$root/runs/$stamp/orchestrator-result.json"
print -r -- "start $repo $(date +%s.%N 2>/dev/null || date +%s) busy=[$ADVANCE_ROADMAP_BUSY_REPOS] lane=$ADVANCE_ROADMAP_LANE" >> "$root/trace.log"
sleep "${FAKE_SLEEP:-3}"
print -r -- "end $repo" >> "$root/trace.log"
print -r -- "{\"stamp\":\"$stamp\",\"outcome\":\"shipped\",\"repo\":\"$repo\",\"pr_url\":\"https://x/$repo\"}" >> "$root/runs.jsonl"
'''

FAKE_USAGE = '''import sys
cmd = sys.argv[-1]
print({"pick-orchestrator": "codex", "pick-worker-chain": "cursor"}.get(cmd, ""))
'''


class SupervisorTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "lib").mkdir()
        (self.root / "lib" / "usage.py").write_text(FAKE_USAGE)
        for name in ("verdict.py", "linearsnap.py", "workspaces.py"):
            (self.root / "lib" / name).write_text("import sys; sys.exit(0)\n")
        run = self.root / "run.sh"
        run.write_text(FAKE_RUN)
        run.chmod(0o755)

    def tearDown(self):
        self.tmp.cleanup()

    def supervise(self, repos, dispatch_s=8, sleep_s=3, lanes=8):
        now = datetime.now()
        # The window string has minute resolution, so compute the cutoff from the same rounding.
        start = (now - timedelta(minutes=1)).replace(second=0, microsecond=0)
        end = (now + timedelta(minutes=3)).replace(second=0, microsecond=0)
        cutoff_min = (end - now).total_seconds() / 60 - dispatch_s / 60
        env = {**os.environ,
               "ADVANCE_ROADMAP_ROOT": str(self.root), "PERSONAL_CODE_DIR": str(self.root),
               "ADVANCE_ROADMAP_WINDOWS": f"{start:%H:%M}-{end:%H:%M}",
               "ADVANCE_ROADMAP_DISPATCH_CUTOFF_MIN": str(cutoff_min),
               "ADVANCE_ROADMAP_SUPERVISOR_POLL_S": "0.3", "ADVANCE_ROADMAP_SUPERSET": "0",
               "FAKE_REPOS": " ".join(repos), "FAKE_SLEEP": str(sleep_s)}
        t0 = time.time()
        p = subprocess.run([sys.executable, str(HERE / "supervisor.py"), "--lanes", str(lanes)],
                           env=env, capture_output=True, text=True, timeout=120)
        self.elapsed = time.time() - t0
        self.out = p.stdout + p.stderr
        return p.returncode

    def trace(self):
        events, held, peak = [], set(), 0
        for line in (self.root / "trace.log").read_text().splitlines():
            kind, repo = line.split()[:2]
            if kind == "start":
                self.assertNotIn(repo, held, f"two lanes on {repo} at once\n{self.out}")
                held.add(repo)
                peak = max(peak, len(held))
            else:
                held.discard(repo)
            events.append(line)
        return events, peak

    def test_parallel_lanes_one_per_repo(self):
        self.assertEqual(self.supervise(["a", "b", "c"]), 0, self.out)
        events, peak = self.trace()
        self.assertEqual(peak, 3, self.out)                      # one lane per available repo
        starts = [e for e in events if e.startswith("start")]
        self.assertTrue(any("busy=[a]" in e or "busy=[a b]" in e for e in starts), starts)
        state = json.loads((self.root / "supervisor-state.json").read_text())
        self.assertEqual(state["phase"], "done")
        self.assertGreaterEqual(state["shipped"], 3)

    def test_lane_cap(self):
        self.supervise(["a", "b", "c", "d"], lanes=2)
        _, peak = self.trace()
        self.assertEqual(peak, 2, self.out)

    def test_stops_at_cutoff_and_releases_lock(self):
        self.supervise(["a"], dispatch_s=4, sleep_s=1)
        self.assertLess(self.elapsed, 60, self.out)              # did not idle to the window's end
        self.assertFalse((self.root / "run.lock").exists())
        self.assertIn("supervisor done", self.out)

    def test_no_work_stops_new_lanes(self):
        # Nothing to do anywhere: one lane finds no work, and no more start until things change.
        self.supervise([], dispatch_s=5)
        rows = (self.root / "runs.jsonl").read_text().splitlines()
        self.assertEqual(len(rows), 1, self.out)

    def test_outside_windows_does_nothing(self):
        env = {**os.environ, "ADVANCE_ROADMAP_ROOT": str(self.root), "PERSONAL_CODE_DIR": str(self.root),
               "ADVANCE_ROADMAP_WINDOWS": "00:00-00:01"}
        if datetime.now().hour < 2:
            self.skipTest("too close to the test window")
        p = subprocess.run([sys.executable, str(HERE / "supervisor.py")], env=env,
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(p.returncode, 0)
        self.assertFalse((self.root / "logs").exists())        # a catch-up fire leaves no trace


if __name__ == "__main__":
    unittest.main()
