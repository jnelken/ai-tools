"""Exact-session ownership checks; no provider calls or account data."""
from pathlib import Path
import json
import os
import signal
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import continuation
import session_stream
import workqueue


class ContinuationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        self.git('init', '-q', '-b', 'roadmap/test')
        self.git('config', 'user.name', 'Test')
        self.git('config', 'user.email', 'test@example.invalid')
        self.git('commit', '--allow-empty', '-qm', 'initial')
        self.request = {'stamp': 'one', 'orchestrator': {'action': 'dispatch_worker',
            'repo': 'example', 'item': 'item 1', 'branch': 'roadmap/test'}}
        self.env = {'CODEX_HOME': str(self.path / 'auth'), 'OPENAI_API_KEY': 'account-one'}

    def git(self, *args):
        subprocess.run(['git', '-C', str(self.path), *args], check=True, capture_output=True)

    def test_exact_identity_and_compatibility(self):
        saved = continuation.binding(self.request, 'codex', self.path, 'session-1', self.env)
        self.assertTrue(continuation.compatible(saved, self.request, 'codex', self.path, self.env))
        resumed = {**self.request, 'stamp': 'two', 'orchestrator': {
            **self.request['orchestrator'], 'action': 'resume_worker'}}
        self.assertTrue(continuation.compatible(saved, resumed, 'codex', self.path, self.env))
        self.assertFalse(continuation.compatible(saved, resumed, 'claude', self.path, self.env))
        self.assertFalse(continuation.compatible(saved, resumed, 'codex', self.path,
                                                  {**self.env, 'OPENAI_API_KEY': 'account-two'}))
        other = {**resumed, 'orchestrator': {**resumed['orchestrator'], 'item': 'item 2'}}
        self.assertFalse(continuation.compatible(saved, other, 'codex', self.path, self.env))
        changed = {**resumed, 'orchestrator': {**resumed['orchestrator'], 'worker_brief': 'new requirements'}}
        self.assertFalse(continuation.compatible(saved, changed, 'codex', self.path, self.env))
        self.git('commit', '--allow-empty', '-qm', 'changed head')
        self.assertFalse(continuation.compatible(saved, resumed, 'codex', self.path, self.env))

    def test_missing_auth_or_wrong_branch_fails_closed(self):
        self.assertIsNone(continuation.binding(self.request, 'codex', self.path, 'id',
                                               {'CODEX_HOME': str(self.path / 'missing')}))
        self.git('checkout', '-qb', 'different')
        self.assertIsNone(continuation.binding(self.request, 'codex', self.path, 'id', self.env))

    def test_session_is_durable_and_stale_attempt_cannot_replace_it(self):
        saved = continuation.binding(self.request, 'codex', self.path, 'session-1', self.env)
        with workqueue.Store(self.path / 'state') as store:
            store.register(self.request['orchestrator'], 'one')
            self.assertTrue(store.save_session(saved))
            store.register(self.request['orchestrator'], 'two')
            self.assertFalse(store.save_session({**saved, 'session_id': 'stale'}))
        with workqueue.Store(self.path / 'state') as store:
            self.assertEqual(store.session(saved['work_id'], 'codex')['session_id'], 'session-1')

    def test_stream_captures_session_before_provider_exits(self):
        root = self.path / 'state'
        request_path = self.path / 'request.json'
        request_path.write_text(json.dumps(self.request))
        with workqueue.Store(root) as store:
            store.register(self.request['orchestrator'], 'one')
        sid = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
        program = ('import json,time; print(json.dumps({"type":"thread.started",'
                   '"thread_id":"' + sid + '"}),flush=True); time.sleep(2)')
        stream = Path(__file__).resolve().parents[1] / 'lib/session_stream.py'
        child = subprocess.Popen([sys.executable, str(stream), '--provider', 'codex',
            '--root', str(root), '--request', str(request_path), '--workspace', str(self.path),
            '--output', str(self.path / 'out'), '--session-out', str(self.path / 'session'),
            '--', sys.executable, '-c', program], env={**os.environ, **self.env})
        try:
            deadline = time.monotonic() + 1.5
            while not (self.path / 'session').exists() and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertIsNone(child.poll())
            self.assertEqual((self.path / 'session').read_text().strip(), sid)
            with workqueue.Store(root) as store:
                self.assertEqual(store.session(workqueue.identity(self.request['orchestrator']),
                                               'codex')['session_id'], sid)
            self.assertEqual(child.wait(timeout=4), 0)
        finally:
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=4)

    def test_watchdog_retains_session_and_distinguishes_forced_stop(self):
        root = self.path / 'state'
        request_path = self.path / 'request.json'
        request_path.write_text(json.dumps(self.request))
        with workqueue.Store(root) as store:
            store.register(self.request['orchestrator'], 'one')
        sid = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'
        program = ('import json,time; print(json.dumps({"type":"thread.started",'
                   '"thread_id":"' + sid + '"}),flush=True); time.sleep(10)')
        stream = Path(__file__).resolve().parents[1] / 'lib/session_stream.py'
        result = subprocess.run([sys.executable, str(stream), '--provider', 'codex',
            '--root', str(root), '--request', str(request_path), '--workspace', str(self.path),
            '--output', str(self.path / 'out'), '--session-out', str(self.path / 'session'),
            '--timeout', '.4', '--lifecycle', str(self.path / 'lifecycle'), '--',
            sys.executable, '-c', program], env={**os.environ, **self.env}, timeout=5)
        self.assertEqual(result.returncode, 124)
        self.assertEqual((self.path / 'session').read_text().strip(), sid)
        info = json.loads((self.path / 'lifecycle').read_text())
        self.assertEqual(info['reason'], 'watchdog')
        self.assertTrue(info['forced'])

    def test_independent_guard_stops_provider_if_supervisor_is_killed(self):
        root = self.path / 'state'
        request_path = self.path / 'request.json'
        request_path.write_text(json.dumps(self.request))
        with workqueue.Store(root) as store:
            store.register(self.request['orchestrator'], 'one')
        pid_path = self.path / 'provider.pid'
        sid = 'eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee'
        program = ('import json,os,time; open(' + repr(str(pid_path)) + ',"w").write(str(os.getpid())); '
                   'print(json.dumps({"type":"thread.started","thread_id":"' + sid + '"}),flush=True); time.sleep(30)')
        stream = Path(__file__).resolve().parents[1] / 'lib/session_stream.py'
        supervisor = subprocess.Popen([sys.executable, str(stream), '--provider', 'codex',
            '--root', str(root), '--request', str(request_path), '--workspace', str(self.path),
            '--output', str(self.path / 'out'), '--session-out', str(self.path / 'session'),
            '--timeout', '20', '--', sys.executable, '-c', program], env={**os.environ, **self.env})
        provider = None
        try:
            deadline = time.monotonic() + 4
            while (not pid_path.exists() or not (self.path / 'session').exists()) and time.monotonic() < deadline:
                time.sleep(.02)
            provider = int(pid_path.read_text())
            started = workqueue.process_start(provider)
            supervisor.kill()
            supervisor.wait(timeout=2)
            deadline = time.monotonic() + 4
            while workqueue.process_start(provider) == started and time.monotonic() < deadline:
                time.sleep(.1)
            self.assertNotEqual(workqueue.process_start(provider), started)
        finally:
            if supervisor.poll() is None:
                supervisor.kill()
                supervisor.wait(timeout=2)
            if provider:
                try:
                    os.killpg(provider, signal.SIGKILL)
                except ProcessLookupError:
                    pass

    def test_live_quota_yield_includes_other_reservations_and_reset_handling(self):
        baseline = {'windows': {'five_hour': {'live': 7}}}
        reading = {'windows': {'five_hour': {'live': 17, 'cap': 70}}}
        reason, metrics = session_stream.quota_decision(reading, baseline, {'five_hour': 10}, {})
        self.assertEqual(reason, 'quota-target')
        self.assertEqual(metrics['five_hour']['account_delta'], 10)
        reading['windows']['five_hour']['live'] = 65
        self.assertEqual(session_stream.quota_decision(reading, baseline, {'five_hour': 90},
                                                       {'five_hour': 5})[0], 'quota-ceiling')
        reading['windows']['five_hour']['live'] = 2
        self.assertIsNone(session_stream.quota_decision(reading, baseline, {'five_hour': 10}, {})[0])

    def test_live_quota_target_stops_a_running_fake_provider(self):
        here = Path(__file__).resolve().parents[1]
        root, fake_lib = self.path / 'runtime', self.path / 'fake-lib'
        (root / 'lib').mkdir(parents=True)
        fake_lib.mkdir()
        for name in ('workqueue.py', 'continuation.py', 'session_stream.py', 'session_guard.py'):
            shutil.copy(here / 'lib' / name, root / 'lib' / name)
        (fake_lib / 'agent_budget.py').write_text('import json,os\nfrom pathlib import Path\n'
            'def readings(*a,**kw): return json.load(open(os.environ["FAKE_READING_FILE"]))\n'
            'def budget_dir(): return Path(os.environ["FAKE_BUDGET_DIR"])\n'
            'def read_json(*a): return []\n'
            'def lease_pct(*a): return 0\n')
        request_path = self.path / 'request.json'
        request_path.write_text(json.dumps(self.request))
        with workqueue.Store(root) as store:
            store.register(self.request['orchestrator'], 'one')
        reading = {'observed_at': time.time(), 'source': 'fake',
                   'windows': {'five_hour': {'live': 7, 'cap': 70}}}
        reading_path = self.path / 'reading.json'
        reading_path.write_text(json.dumps(reading))
        budget_path = self.path / 'budget.json'
        budget_path.write_text(json.dumps({'baseline': reading, 'lease_ids': ['own'],
                                           'worker_reservation': {'five_hour': 10}}))
        sid = 'ffffffff-ffff-4fff-8fff-ffffffffffff'
        program = ('import json,time; print(json.dumps({"type":"thread.started",'
                   '"thread_id":"' + sid + '"}),flush=True); time.sleep(30)')
        env = {**os.environ, **self.env, 'PYTHONPATH': str(fake_lib),
               'FAKE_READING_FILE': str(reading_path), 'FAKE_BUDGET_DIR': str(fake_lib)}
        child = subprocess.Popen([sys.executable, str(root / 'lib/session_stream.py'), '--provider', 'codex',
            '--root', str(root), '--request', str(request_path), '--workspace', str(self.path),
            '--output', str(self.path / 'out'), '--session-out', str(self.path / 'session'),
            '--timeout', '20', '--quota-poll', '.1', '--budget', str(budget_path),
            '--lifecycle', str(self.path / 'lifecycle'), '--', sys.executable, '-c', program], env=env)
        try:
            deadline = time.monotonic() + 4
            while not (self.path / 'session').exists() and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertTrue((self.path / 'session').exists())
            reading['windows']['five_hour']['live'] = 17
            reading_path.write_text(json.dumps(reading))
            self.assertEqual(child.wait(timeout=5), 124)
            result = json.loads((self.path / 'lifecycle').read_text())
            self.assertEqual(result['reason'], 'quota-target')
            self.assertEqual(result['last_quota']['windows']['five_hour']['account_delta'], 10)
        finally:
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=5)

    def test_worker_resumes_only_its_owned_exact_codex_session(self):
        here = Path(__file__).resolve().parents[1]
        root = self.path / 'runtime'
        (root / 'lib').mkdir(parents=True)
        for name in ('workqueue.py', 'continuation.py', 'session_stream.py', 'session_guard.py', 'tokens.py', 'models.sh'):
            shutil.copy(here / 'lib' / name, root / 'lib' / name)
        (root / 'lib/usage.py').write_text('import json,os,sys\n'
            'if "gate" in sys.argv and os.environ.get("FAKE_OVER"):\n'
            ' print(json.dumps({"state":os.environ.get("FAKE_GATE_STATE","over"),"reason":"5h 85% >= 70%"})); sys.exit(3)\n'
            'sys.exit(1 if "live-limit" in sys.argv else 0)\n')
        sid = 'cccccccc-cccc-4ccc-8ccc-cccccccccccc'
        fake = self.path / 'fake-codex'
        fake.write_text('#!/usr/bin/env python3\n'
            'import json,os,sys,time\n'
            'open(os.environ["CAPTURE"],"w").write(json.dumps(sys.argv[1:]))\n'
            'if os.environ.get("FAKE_MISSING") and "resume" in sys.argv:\n'
            ' print("No rollout found for this session",file=sys.stderr); sys.exit(1)\n'
            'print(json.dumps({"type":"thread.started","thread_id":"' + sid + '"}),flush=True)\n'
            'if os.environ.get("FAKE_SLEEP"):\n'
            ' print("Authentication required",flush=True); time.sleep(10)\n'
            'open(sys.argv[sys.argv.index("-o")+1],"w").write("done")\n'
            'open(os.environ["RESULT"],"w").write(json.dumps({"outcome":"yielded",'
            '"provider":"codex","summary":"milestone","next_phase":"reviewing"}))\n')
        fake.chmod(0o755)
        request = {**self.request, 'stamp': 'two', 'mode': 'resume', 'orchestrator': {
            **self.request['orchestrator'], 'action': 'resume_worker'}}
        request_path = self.path / 'request.json'
        request_path.write_text(json.dumps(request))
        prior = continuation.binding(self.request, 'codex', self.path, sid, self.env)
        with workqueue.Store(root) as store:
            store.register(self.request['orchestrator'], 'one')
            store.save_session(prior)
            store.register(request['orchestrator'], 'two')
        result_path, status = self.path / 'result.json', self.path / 'status.json'
        env = {**os.environ, **self.env, 'PERSONAL_CODE_DIR': str(self.path),
            'SUPERSET_WORKSPACE_PATH': str(self.path), 'ADVANCE_ROADMAP_ROOT': str(root),
            'ADVANCE_ROADMAP_SKILL_DIR': str(here.parents[1] / 'skills/advance-roadmap'),
            'ADVANCE_ROADMAP_CODEX_BIN': str(fake), 'ADVANCE_ROADMAP_INCREMENTAL': '1',
            'ADVANCE_ROADMAP_BUDGET_REVIEW_STRATEGY': 'cross-provider',
            'CAPTURE': str(self.path / 'args'), 'RESULT': str(result_path)}
        result = subprocess.run(['/bin/zsh', str(here / 'worker.sh'), '--stamp', 'two',
            '--request-file', str(request_path), '--providers', 'codex', '--result-file', str(result_path),
            '--status-file', str(status)], env=env, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        args = json.loads((self.path / 'args').read_text())
        self.assertEqual(args[args.index('resume') + 1], sid)
        self.assertNotIn('--last', args)
        prompt = args[-1]
        self.assertIn('leave the branch unmerged', prompt.lower())
        self.assertNotIn('open/merge the pr', prompt.lower())
        self.assertEqual(json.loads((self.path / 'continuation-codex.json').read_text())['mode'], 'native')
        env['FAKE_MISSING'] = '1'
        result = subprocess.run(['/bin/zsh', str(here / 'worker.sh'), '--stamp', 'two',
            '--request-file', str(request_path), '--providers', 'codex', '--result-file', str(result_path),
            '--status-file', str(status)], env=env, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn('resume', json.loads((self.path / 'args').read_text()))
        self.assertEqual(json.loads((self.path / 'continuation-codex.json').read_text())['mode'], 'fallback')
        env.pop('FAKE_MISSING')
        # An authorized override admits only its leased provider and still runs
        # the live monitor. The fake budget module avoids account probes.
        fake_lib = self.path / 'fake-lib'
        fake_lib.mkdir()
        (fake_lib / 'agent_budget.py').write_text('import json,os\nfrom pathlib import Path\n'
            'def readings(*a,**kw): return json.loads(os.environ["FAKE_READING"])\n'
            'def budget_dir(): return Path(os.environ["FAKE_BUDGET_DIR"])\n'
            'def read_json(*a): return []\n'
            'def lease_pct(*a): return 0\n')
        reading = {'observed_at': time.time(), 'source': 'fake', 'windows': {
            'five_hour': {'live': 85, 'cap': 100}, 'weekly': {'live': 20, 'cap': 100}}}
        (self.path / 'batch-budget.json').write_text(json.dumps({'worker': 'codex', 'job_override': True,
            'baseline': reading, 'lease_ids': ['own'], 'worker_reservation': {'five_hour': 10, 'weekly': 1}}))
        env.update(FAKE_OVER='1', ADVANCE_ROADMAP_JOB_OVERRIDE='1', ADVANCE_ROADMAP_BUDGET_WORKER='codex',
                   FAKE_READING=json.dumps(reading), FAKE_BUDGET_DIR=str(fake_lib), PYTHONPATH=str(fake_lib))
        result = subprocess.run(['/bin/zsh', str(here / 'worker.sh'), '--stamp', 'two',
            '--request-file', str(request_path), '--providers', 'codex', '--result-file', str(result_path),
            '--status-file', str(status)], env=env, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('job-only override admitted', result.stdout)
        (self.path / 'args').unlink()
        env['FAKE_GATE_STATE'] = 'limited'
        result = subprocess.run(['/bin/zsh', str(here / 'worker.sh'), '--stamp', 'two',
            '--request-file', str(request_path), '--providers', 'codex', '--result-file', str(result_path),
            '--status-file', str(status)], env=env, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 3)
        self.assertFalse((self.path / 'args').exists())
        env.pop('FAKE_OVER')
        env.pop('ADVANCE_ROADMAP_JOB_OVERRIDE')
        env.update(FAKE_SLEEP='1', ADVANCE_ROADMAP_CHUNK_TIMEOUT_S='.4')
        result = subprocess.run(['/bin/zsh', str(here / 'worker.sh'), '--stamp', 'two',
            '--request-file', str(request_path), '--providers', 'codex', '--result-file', str(result_path),
            '--status-file', str(status)], env=env, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 124, result.stdout + result.stderr)
        self.assertEqual(json.loads(status.read_text())['missing'], [])

    def test_provider_switch_uses_fallback_then_owns_claude_session(self):
        here = Path(__file__).resolve().parents[1]
        root, ai_home = self.path / 'runtime', self.path / 'ai'
        (root / 'lib').mkdir(parents=True)
        (ai_home / 'bin').mkdir(parents=True)
        for name in ('workqueue.py', 'continuation.py', 'session_stream.py', 'session_guard.py', 'tokens.py', 'models.sh'):
            shutil.copy(here / 'lib' / name, root / 'lib' / name)
        (root / 'lib/usage.py').write_text('import sys\nsys.exit(1 if "live-limit" in sys.argv else 0)\n')
        wrapper = ai_home / 'bin/claude-session'
        wrapper.write_text('import os,sys\na=sys.argv[sys.argv.index("--")+1:]\nos.execvp(a[0],a)\n')
        sid = 'dddddddd-dddd-4ddd-8ddd-dddddddddddd'
        fake = self.path / 'fake-claude'
        fake.write_text('#!/usr/bin/env python3\nimport json,os,sys\n'
            'open(os.environ["CAPTURE"],"w").write(json.dumps(sys.argv[1:]))\n'
            'print(json.dumps({"type":"system","subtype":"init","session_id":"' + sid + '"}),flush=True)\n'
            'print(json.dumps({"type":"result","result":"done","session_id":"' + sid + '"}))\n'
            'open(os.environ["RESULT"],"w").write(json.dumps({"outcome":"yielded",'
            '"provider":"claude","summary":"milestone","next_phase":"reviewing"}))\n')
        fake.chmod(0o755)
        request = {**self.request, 'stamp': 'two', 'mode': 'resume', 'orchestrator': {
            **self.request['orchestrator'], 'action': 'resume_worker'}}
        request_path = self.path / 'request.json'
        request_path.write_text(json.dumps(request))
        prior = continuation.binding(self.request, 'codex', self.path,
                                     'cccccccc-cccc-4ccc-8ccc-cccccccccccc', self.env)
        with workqueue.Store(root) as store:
            store.register(self.request['orchestrator'], 'one')
            store.save_session(prior)
            store.register(request['orchestrator'], 'two')
        result_path, status = self.path / 'result.json', self.path / 'status.json'
        env = {**os.environ, **self.env, 'ANTHROPIC_API_KEY': 'account-one',
            'PERSONAL_CODE_DIR': str(self.path), 'AI_TOOLS_HOME': str(ai_home),
            'SUPERSET_WORKSPACE_PATH': str(self.path), 'ADVANCE_ROADMAP_ROOT': str(root),
            'ADVANCE_ROADMAP_SKILL_DIR': str(here.parents[1] / 'skills/advance-roadmap'),
            'ADVANCE_ROADMAP_CLAUDE_BIN': str(fake), 'ADVANCE_ROADMAP_INCREMENTAL': '1',
            'ADVANCE_ROADMAP_BUDGET_REVIEW_STRATEGY': 'cross-provider',
            'CAPTURE': str(self.path / 'args'), 'RESULT': str(result_path)}
        result = subprocess.run(['/bin/zsh', str(here / 'worker.sh'), '--stamp', 'two',
            '--request-file', str(request_path), '--providers', 'claude', '--result-file', str(result_path),
            '--status-file', str(status)], env=env, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn('--resume', json.loads((self.path / 'args').read_text()))
        self.assertEqual(json.loads((self.path / 'continuation-claude.json').read_text())['mode'], 'fallback')
        with workqueue.Store(root) as store:
            self.assertEqual(store.session(workqueue.identity(request['orchestrator']), 'claude')['session_id'], sid)
            store.register(request['orchestrator'], 'three')
        request['stamp'] = 'three'
        request_path.write_text(json.dumps(request))
        result = subprocess.run(['/bin/zsh', str(here / 'worker.sh'), '--stamp', 'three',
            '--request-file', str(request_path), '--providers', 'claude', '--result-file', str(result_path),
            '--status-file', str(status)], env=env, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        args = json.loads((self.path / 'args').read_text())
        self.assertEqual(args[args.index('--resume') + 1], sid)
        self.assertEqual(json.loads((self.path / 'continuation-claude.json').read_text())['mode'], 'native')


if __name__ == '__main__':
    unittest.main()
