"""Budget tests use isolated account roots and never contact provider services."""
import argparse
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import agent_budget as b
import agent_fanout as f

spec = importlib.util.spec_from_file_location('budget_hook', b.ROOT / 'hooks/agent-budget-gate.py')
hook = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hook)


class BudgetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.env = patch.dict(os.environ, {'HOME': str(self.home), 'AGENT_BUDGET_CLAUDE_HOME': str(self.home / '.claude'),
            'ADVANCE_ROADMAP_CODEX_SESSIONS': str(self.home / '.codex/sessions'),
            'ADVANCE_ROADMAP_USAGE_PROBES': '0', 'ANTHROPIC_API_KEY': 'test-disable-oauth'})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.reading = {'source': 'fixture', 'windows': {'five_hour': {'live': 50, 'cap': 70},
                                                       'weekly': {'live': 60, 'cap': 80}}}
        self.readings = patch.object(b, 'readings', return_value=self.reading)
        self.readings.start()
        self.addCleanup(self.readings.stop)

    def append(self, path, records):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(''.join(json.dumps(r) + '\n' for r in records))

    def leases(self):
        with b.lock():
            return b.reap()

    def test_p80_and_role_aliases(self):
        self.assertEqual(b.pctl([1, 2, 3, 4, 5]), 4)
        self.assertIsNone(b.pctl([]))
        records = [{'provider': 'codex', 'role': 'orchestrator', 'total': v} for v in [10, 20, 30, 40, 50]]
        model = b.estimates(records, [])['codex']
        self.assertEqual(model['roles']['planner']['tokens_p80'], 40)
        self.assertEqual(model['roles']['reviewer']['tokens_p80'], 29000)

    def test_seeds_until_five_calibrated_samples(self):
        samples = [{'provider': 'codex', 'tokens': 1000, 'pct': i} for i in range(1, 6)]
        self.assertEqual(b.estimates([], samples[:4])['codex']['factor'], 3 / 70000)
        model = b.estimates([], samples)['codex']
        self.assertEqual(model['factor_source'], 'calibrated')
        self.assertEqual(model['factor'], .004)
        self.assertAlmostEqual(b.estimates([], [])['claude']['roles']['interactive']['pct'], 10)

    def test_headroom_and_lease_admission(self):
        self.assertEqual(b.allow('codex', 'planner', 99), 6)
        lease = b.acquire('codex', 'planner')
        self.assertAlmostEqual(lease['pct'], 3)
        self.assertEqual(b.allow('codex', 'planner', 99), 5)
        status = b.view('codex', self.reading, {}, self.leases())
        self.assertEqual(status['windows']['five_hour']['headroom'], 17)
        self.reading['windows']['weekly']['live'] = 79
        self.assertEqual(b.allow('codex', 'planner', 99), 0)
        b.release(lease['id'])
        self.assertEqual(self.leases(), [])

    def test_unknown_allows_one_lease_per_provider(self):
        for window in self.reading['windows'].values():
            window['live'] = None
        self.assertEqual(b.allow('codex', 'planner', 99), 1)
        self.assertIsNotNone(b.acquire('codex', 'planner'))
        self.assertEqual(b.allow('codex', 'reviewer', 99), 0)
        self.assertEqual(b.allow('claude', 'subagent', 99), 1)
        self.reading['windows']['weekly']['live'] = 81
        self.assertEqual(b.allow('claude', 'subagent', 99), 0)

    def test_dead_pids_and_expired_ttl_reaped_on_read(self):
        live = b.acquire('codex', 'planner')
        dead = dict(live, id='dead', pid=99999999)
        expired = dict(live, id='expired', created=time.time() - 100, ttl=1)
        b.write_json(b.budget_dir() / 'leases.json', [live, dead, expired])
        self.assertEqual([l['id'] for l in self.leases()], [live['id']])
        self.assertEqual(len(b.read_json(b.budget_dir() / 'leases.json')), 1)

    def test_run_nonzero_and_spawn_failure_release(self):
        self.assertEqual(b.run('codex', 'planner', [sys.executable, '-c', 'import sys; sys.exit(7)']), 7)
        self.assertEqual(self.leases(), [])
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(b.run('codex', 'planner', ['/no/such/cli']), 127)
        self.assertEqual(self.leases(), [])

    def test_run_sigterm_releases_and_forwards_to_process_group(self):
        ready = self.home / 'ready'
        script = '''import sys,os
sys.path.insert(0, sys.argv[1])
import agent_budget as b
b.readings = lambda p: {'windows': {'five_hour': {'live': 0, 'cap': 70}}}
sys.exit(b.run('codex', 'planner', [sys.executable, '-c', 'import pathlib,time; pathlib.Path('+repr(sys.argv[2])+').write_text("ready"); time.sleep(30)']))
'''
        process = subprocess.Popen([sys.executable, '-c', script, str(b.ROOT / 'lib'), str(ready)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.addCleanup(lambda: process.poll() is None and process.kill())
        deadline = time.time() + 5
        while not ready.exists() and time.time() < deadline:
            time.sleep(.02)
        self.assertTrue(ready.exists())
        process.send_signal(signal.SIGTERM)
        _, err = process.communicate(timeout=5)
        self.assertEqual(process.returncode, 143, err)
        self.assertEqual(self.leases(), [])

    def test_waiting_run_cancels_without_starting_model_or_leaking_lease(self):
        ready = self.home / 'waiting'
        script = '''import sys
sys.path.insert(0, sys.argv[1])
import agent_budget as b
from pathlib import Path
b.acquire = lambda *a, **kw: (Path(sys.argv[2]).write_text('waiting') and None)
sys.exit(b.run('codex', 'planner', ['/must/not/launch'], wait=True))
'''
        process = subprocess.Popen([sys.executable, '-c', script, str(b.ROOT / 'lib'), str(ready)],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.addCleanup(lambda: process.poll() is None and process.kill())
        deadline = time.time() + 5
        while not ready.exists() and time.time() < deadline:
            time.sleep(.02)
        self.assertTrue(ready.exists())
        process.send_signal(signal.SIGTERM)
        process.communicate(timeout=5)
        self.assertEqual(process.returncode, 143)
        self.assertEqual(self.leases(), [])

    def test_atomic_admission_under_concurrency(self):
        # Ten processes contend for the same unknown provider, and assign the
        # durable lease to this test's pid so none disappears between contenders.
        script = '''import sys,json
sys.path.insert(0, sys.argv[1])
import agent_budget as b
b.readings = lambda p: {'windows': {'five_hour': {'live': None, 'cap': 70}}}
print(bool(b.acquire('codex','planner',pid=int(sys.argv[2]))))
'''
        processes = [subprocess.Popen([sys.executable, '-c', script, str(b.ROOT / 'lib'), str(os.getpid())],
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(10)]
        outputs = [p.communicate(timeout=10)[0].strip() for p in processes]
        self.assertEqual(outputs.count('True'), 1)
        self.assertEqual(len(self.leases()), 1)

    def test_headless_environment_strips_hooks_and_parent_identity(self):
        with patch.dict(os.environ, {'SUPERSET_AGENT_HOOK_PORT': 'private', 'SUPERSET_AGENT_ID': 'outer',
                'SUPERSET_HOST_AGENT_HOOK_URL': 'private', 'SUPERSET_TERMINAL_ID': 'parent',
                'SUPERSET_WORKSPACE_ID': 'workspace', 'SUPERSET_ACCOUNT_ATTRIBUTION_TOKEN': 'keep', 'CLAUDECODE': '1'}):
            env = b.child_env()
            self.assertNotIn('SUPERSET_AGENT_HOOK_PORT', env)
            self.assertNotIn('SUPERSET_HOST_AGENT_HOOK_URL', env)
            self.assertNotIn('SUPERSET_AGENT_ID', env)
            self.assertNotIn('SUPERSET_TERMINAL_ID', env)
            self.assertNotIn('CLAUDECODE', env)
            self.assertEqual(env['SUPERSET_WORKSPACE_ID'], 'workspace')
            self.assertEqual(env['SUPERSET_ACCOUNT_ATTRIBUTION_TOKEN'], 'keep')

    def test_nested_session_end_cannot_dispatch_via_superset_tab_fallback(self):
        identity = {'SUPERSET_TAB_ID': 'parent-tab', 'SUPERSET_PANE_ID': 'parent-pane',
                    'SUPERSET_TERMINAL_ID': 'parent-terminal', 'SUPERSET_AGENT_ID': 'claude',
                    'SUPERSET_AGENT_LAUNCH_ID': 'parent-launch', 'SUPERSET_AGENT_HOOK_PORT': '1234',
                    'SUPERSET_AGENT_HOOK_VERSION': '2', 'SUPERSET_HOST_AGENT_HOOK_URL': 'private',
                    'SUPERSET_HOOK_HARNESS': 'claude', 'SUPERSET_HOOK_VERSION': '20',
                    'SUPERSET_HOOK_DEBUG_LOG': str(self.home / 'hook.log'), 'SUPERSET_PORT': '1234',
                    'SUPERSET_DEBUG_HOOKS': '1'}
        # notify.sh v20 line 18 admits either a terminal OR a tab. This routing
        # fixture emits a marker instead of curl, so the regression is offline.
        notify = 'cat >/dev/null; [ -n "$SUPERSET_TERMINAL_ID" ] || [ -n "$SUPERSET_TAB_ID" ] || exit 0; printf Stop'
        payload = json.dumps(dict(hook_event_name='SessionEnd', session_id='nested'))
        with patch.dict(os.environ, identity):
            control = subprocess.run(['sh', '-c', notify], input=payload, text=True, capture_output=True)
            self.assertEqual(control.stdout, 'Stop')
            env = b.child_env()
            for key in identity:
                self.assertNotIn(key, env)
            nested = subprocess.run(['sh', '-c', notify], input=payload, text=True, capture_output=True, env=env)
            self.assertEqual(nested.returncode, 0)
            self.assertEqual(nested.stdout, '')
            self.assertFalse((self.home / 'hook.log').exists())

    def test_history_ledgers_transcripts_and_snapshot_calibration(self):
        ch = b.claude_home()
        self.append(ch / 'automations/advance-roadmap/runs.jsonl', [
            {'tokens': {'calls': [{'provider': 'codex', 'role': 'orchestrator', 'total': 71000}]}}])
        self.append(ch / 'state/agent-usage.jsonl', [
            {'provider': 'claude', 'role': 'worker', 'tokens': {'total': 250000}, 'session_id': 'sid'}])
        transcript = ch / 'projects/project/sid/subagents/agent-child.jsonl'
        self.append(transcript, [dict(type='assistant', uuid='a', message=dict(id='msg', usage={
            'input_tokens': 2, 'cache_creation_input_tokens': 100, 'cache_read_input_tokens': 999, 'output_tokens': out}))
            for out in (3, 9)])
        b.write_json(transcript.with_suffix('.meta.json'), {'agentType': 'Explore', 'toolUseId': 'tool1'})
        self.append(ch / 'state/quota-snapshots.jsonl', [
            dict(event=event, session_id='sid', timestamp=event, quota=dict(age_seconds=0, windows={
                'five_hour': dict(used_percentage=pct, resets_at=2000000000)}))
            for event, pct in [('start', 10), ('finish', 14)]])
        records, samples = b.history()
        self.assertEqual(len(records), 3)
        subagent = next(r for r in records if r['role'] == 'subagent')
        self.assertEqual(subagent['total'], 111)
        self.assertEqual(subagent['agent_type'], 'Explore')
        self.assertEqual(samples[0]['tokens'], 250000)
        self.assertEqual(samples[0]['pct'], 4)
        self.assertEqual(len(b.history()[1]), 1)
        self.assertTrue((b.budget_dir() / 'samples.jsonl').exists())

    def quota_rows(self, session, start, finish, before=10, after=13, path=None):
        return [dict(event=event, session_id=session, timestamp=at, transcript_path=str(path) if path else None,
                     quota=dict(age_seconds=0, windows={'five_hour': dict(used_percentage=pct, resets_at=2000000000)}))
                for event, at, pct in [('start', start, before), ('finish', finish, after)]]

    def test_calibration_uses_each_rounds_session_tokens_and_repairs_saved_samples(self):
        ch = b.claude_home()
        self.append(ch / 'automations/advance-roadmap/runs.jsonl', [dict(tokens=dict(calls=[
            dict(provider='claude', role='reviewer', round=i, total=70000, sessions=[sid])
            for i, sid in enumerate(('one', 'two'))]))])
        self.append(ch / 'state/quota-snapshots.jsonl', self.quota_rows('one', 10, 20) + self.quota_rows('two', 30, 40))
        self.append(b.budget_dir() / 'samples.jsonl', [dict(id='quota:one:10', source='quota_snapshots',
                    provider='claude', tokens=140000, pct=3)])
        records, samples = b.history()
        self.assertEqual(records[0]['total'], 140000)  # whole-job estimate
        self.assertEqual([s['tokens'] for s in samples], [70000, 70000])
        self.assertEqual([s['tokens'] for s in b.rows(b.budget_dir() / 'samples.jsonl')], [70000, 70000])
        self.assertEqual(len(b.history()[1]), 2)

    def test_ambiguous_multi_session_totals_are_not_reused_per_session(self):
        ch = b.claude_home()
        self.append(ch / 'automations/advance-roadmap/runs.jsonl', [dict(tokens=dict(calls=[
            dict(provider='claude', role='reviewer', total=140000, sessions=['one', 'two'])]))])
        self.append(ch / 'state/quota-snapshots.jsonl', self.quota_rows('one', 10, 20) + self.quota_rows('two', 30, 40))
        self.assertEqual(b.history()[1], [])

    def test_resumed_transcript_calibration_is_bounded_to_each_snapshot_interval(self):
        ch = b.claude_home()
        path = ch / 'projects/project/resumed.jsonl'
        self.append(path, [dict(type='assistant', timestamp=at, message=dict(id=str(at), usage=dict(input_tokens=tokens)))
                           for at, tokens in [(5, 100000), (12, 70000), (35, 30000), (50, 200000)]])
        self.append(ch / 'state/agent-usage.jsonl', [dict(provider='claude', role='interactive', total=400000, session_id='resumed')])
        self.append(ch / 'state/quota-snapshots.jsonl', self.quota_rows('resumed', 10, 20, path=path)
                    + self.quota_rows('resumed', 30, 40, path=path))
        _, samples = b.history()
        self.assertEqual([s['tokens'] for s in samples], [70000, 30000])
        self.assertEqual([s['tokens'] for s in b.history()[1]], [70000, 30000])

    def test_codex_cache_writes_match_roadmap_accounting(self):
        raw = dict(input_tokens=1000, cached_input_tokens=800, cache_write_input_tokens=50,
                   output_tokens=10, total_tokens=1010)
        self.assertEqual(b.token_total(raw), 260)
        self.assertEqual(b.token_total(dict(usage=raw)), 260)
        path = self.home / 'rollout-writes.jsonl'
        self.append(path, [dict(payload=dict(info=dict(total_token_usage=dict(input_tokens=1000, cached_input_tokens=800,
                              cache_write_input_tokens=w, output_tokens=10)),
                              rate_limits=dict(primary=dict(used_percent=pct, resets_at=2000000000, window_minutes=300))))
                           for w, pct in [(50, 3), (150, 4)]])
        self.assertEqual(b.codex_samples(path)[0]['tokens'], 100)

    def test_reused_pid_does_not_keep_the_previous_owners_lease(self):
        lease = b.acquire('codex', 'planner')
        self.assertEqual(lease['pid_started_at'], b.process_start(os.getpid()))
        old = dict(lease, id='reused', pid_started_at='previous incarnation')
        legacy = {k: v for k, v in dict(lease, id='legacy').items() if k != 'pid_started_at'}
        b.write_json(b.budget_dir() / 'leases.json', [lease, old, legacy])
        self.assertTrue(b.alive(old['pid']))
        self.assertEqual([l['id'] for l in self.leases()], [lease['id']])

    def test_saved_codex_and_cursor_windows_read_zero_after_reset(self):
        self.readings.stop()
        state = b.usage.empty_state()
        state['providers']['codex'].update(observed_epoch=time.time(),
            five_hour=dict(used_pct=100, reset_epoch=time.time()-1),
            weekly=dict(used_pct=0, reset_epoch=time.time()+100))
        state['providers']['cursor']['included'].update(observed_at=time.time(), auto_pct=100,
            api_pct=100, reset_epoch=time.time()-1)
        path = b.claude_home() / 'automations/advance-roadmap/providers-usage.json'
        b.write_json(path, state)
        self.assertEqual(b.readings('codex')['windows']['five_hour']['live'], 0)
        self.assertEqual(b.readings('cursor')['windows']['auto']['live'], 0)
        self.assertEqual(b.readings('cursor')['windows']['api']['live'], 0)
        self.assertEqual(b.allow('codex', 'planner'), 1)
        self.assertEqual(b.allow('cursor', 'worker'), 1)
        state['providers']['codex']['weekly'] = dict(used_pct=100, reset_epoch=time.time()+100)
        state['providers']['cursor']['included']['reset_epoch'] = time.time()+100
        b.write_json(path, state)
        self.assertEqual(b.allow('codex', 'planner'), 0)
        self.assertEqual(b.allow('cursor', 'worker'), 0)
        state['providers']['codex']['observed_epoch'] = time.time()-901
        state['providers']['cursor']['included']['observed_at'] = time.time()-901
        b.write_json(path, state)
        self.assertIsNone(b.readings('codex')['windows']['five_hour']['live'])
        self.assertIsNone(b.readings('cursor')['windows']['auto']['live'])

    def test_real_shaped_oct27_parallel_rollouts_share_one_account_delta(self):
        fixture = json.loads((Path(__file__).parent / 'fixtures/codex-oct27-planners.json').read_text())
        observations = []
        for i, rollout in enumerate(fixture['rollouts']):
            path = self.home / f'rollout-{i}.jsonl'
            self.append(path, rollout)
            observations.extend(b.codex_observations(path))
        sample, = b.codex_calibration(observations)
        self.assertEqual(sample['pct'], 29)
        self.assertGreater(sample['tokens'], 700000)
        factor = sample['pct'] / sample['tokens']
        self.assertTrue(3e-5 < factor < 5e-5, factor)
        model = b.estimates([dict(provider='codex', role='planner', total=88000)],
                            [dict(sample, id=str(i)) for i in range(5)])['codex']
        self.assertLess(model['roles']['planner']['pct'], 4.4)

    def test_codex_flat_intervals_stale_parallel_readings_and_idle_gaps(self):
        obs = [dict(id=str(i), sequence=i, at=t, tokens=tokens, pct=pct, reset=1)
               for i, (t, tokens, pct) in enumerate([
                   (0, 10000, 41), (1, 30000, 41), (2, 30000, 42),
                   (3, 10000, 41), (4, 30000, 44), (1000, 100, 60), (1001, 10000, 61)])]
        samples = b.codex_calibration(obs)
        self.assertEqual([(s['tokens'], s['pct']) for s in samples], [(100000, 3), (10000, 1)])

    def test_codex_samples_exclude_cache_and_window_resets(self):
        path = self.home / 'rollout.jsonl'
        self.append(path, [dict(payload=dict(info=dict(total_token_usage=dict(input_tokens=t, cached_input_tokens=100, output_tokens=10)),
            rate_limits=dict(primary=dict(used_percent=pct, resets_at=reset, window_minutes=300))))
            for t, pct, reset in [(1000, 3, 2000000000), (2000, 4, 2000000000), (3000, 5, 2000000010)]])
        samples = b.codex_samples(path)
        self.assertEqual(len(samples), 1)
        self.assertEqual(samples[0]['tokens'], 1000)
        self.assertEqual(samples[0]['pct'], 1)

    def test_stale_readings_are_unknown_and_claude_uses_live_quota(self):
        self.readings.stop()
        with patch.object(b.claude_quota, 'quota', return_value={'age_seconds': 901, 'windows': {'five_hour': {'used_percentage': 0}}}):
            self.assertIsNone(b.readings('claude')['windows']['five_hour']['live'])
        with patch.object(b.claude_quota, 'quota', return_value={'age_seconds': 0, 'windows': {'five_hour': {'used_percentage': 42}}}):
            self.assertEqual(b.readings('claude')['windows']['five_hour']['live'], 42)
        state = b.usage.empty_state()
        state['providers']['codex'].update(observed_epoch=time.time()-901, five_hour={'used_pct': 0})
        b.write_json(b.claude_home() / 'automations/advance-roadmap/providers-usage.json', state)
        self.assertIsNone(b.readings('codex')['windows']['five_hour']['live'])
        state['providers']['codex']['observed_epoch'] = time.time()
        state['providers']['codex']['five_hour']['used_pct'] = 69
        b.write_json(b.claude_home() / 'automations/advance-roadmap/providers-usage.json', state)
        self.assertEqual(b.readings('codex')['windows']['five_hour']['live'], 69)

    def test_cursor_selects_role_pool(self):
        reading = {'windows': {'auto': {'live': 100, 'cap': 100}, 'api': {'live': 50, 'cap': 100}}}
        with patch.dict(os.environ, ADVANCE_ROADMAP_CURSOR_REVIEW_MODEL='named'):
            self.assertEqual(b.capacity(reading, [], 'cursor', 'reviewer', 5, 20), 10)
            self.assertEqual(b.capacity(reading, [], 'cursor', 'worker', 5, 20), 0)

    def test_fanout_dry_run_exact_command_and_no_state_mutation(self):
        prompt = self.home / 'prompt'
        prompt.write_text('hello $world; `literal`')
        out = io.StringIO()
        args = ['spawn', '--provider', 'codex', '--role', 'planner', '--label', 'test', '--workspace', 'ws',
                '--prompt-file', str(prompt), '--model', 'test-model', '--effort', 'high', '--dry-run']
        with contextlib.redirect_stdout(out), patch.object(f.uuid, 'uuid4') as uid:
            uid.return_value.hex = 'job'
            self.assertEqual(f.main(args), 0)
        data = json.loads(out.getvalue())
        job = f.state_dir() / 'job'
        import shlex
        self.assertEqual(data['command'], [f.SUPERSET, 'terminals', 'create', '--workspace', 'ws', '--local',
            '--cwd', os.getcwd(), '--command', shlex.join([str(f.FANOUT), '_run', str(job)]), '--json'])
        self.assertEqual(data['budget_command'], [str(f.BUDGET), 'run', '--wait', '--provider', 'codex', '--role', 'planner',
            '--label', 'test', '--', 'codex', 'exec', '--json', '--model', 'test-model', '-c',
            'model_reasoning_effort="high"', 'hello $world; `literal`'])
        self.assertFalse(f.state_dir().exists())

    def test_fanout_resolves_workspace_for_repo_subdirectory_and_worktree(self):
        repo, worktree = self.home / 'repo', self.home / 'worktree'
        for directory in (repo, worktree):
            subprocess.run(['git', 'init', '-q', str(directory)], check=True, capture_output=True)
            (directory / 'src').mkdir()
        alias = self.home / 'repo-alias'
        alias.symlink_to(repo, target_is_directory=True)
        workspaces = [dict(id='parent', worktreePath=str(self.home)), dict(id='repo', worktreePath=str(repo)),
                      dict(id='worktree', worktreePath=str(worktree)),
                      dict(id='archived', worktreePath=str(worktree), archivedAt=1)]
        actual_run = subprocess.run
        def command(argv, **kwargs):
            if argv[0] == f.SUPERSET:
                self.assertEqual(argv, [f.SUPERSET, 'workspaces', 'list', '--local', '--json'])
                return subprocess.CompletedProcess(argv, 0, json.dumps(workspaces))
            return actual_run(argv, **kwargs)
        with patch.object(f.subprocess, 'run', side_effect=command):
            for cwd, expected in [(repo / 'src', 'repo'), (alias / 'src', 'repo'), (worktree / 'src', 'worktree')]:
                self.assertEqual(f.resolve_workspace(cwd), expected)
            prompt = self.home / 'prompt'
            prompt.write_text('hello')
            out = io.StringIO()
            with patch.dict(os.environ, SUPERSET_WORKSPACE_ID=''), patch.object(f.os, 'getcwd', return_value=str(worktree / 'src')), contextlib.redirect_stdout(out):
                self.assertEqual(f.main(['spawn', '--provider', 'codex', '--role', 'planner', '--label', 'fallback',
                                         '--prompt-file', str(prompt), '--dry-run']), 0)
            self.assertEqual(json.loads(out.getvalue())['command'][4], 'worktree')
        self.assertFalse(f.state_dir().exists())

    def test_workspace_resolution_reports_no_match_and_list_failure(self):
        with patch.object(f.subprocess, 'run') as run:
            run.side_effect = [subprocess.CompletedProcess([], 128, ''), subprocess.CompletedProcess([], 0, '[]')]
            with self.assertRaisesRegex(ValueError, 'no local Superset workspace matches.*--workspace ID'):
                f.resolve_workspace(self.home)
            run.side_effect = [subprocess.CompletedProcess([], 128, ''), subprocess.CalledProcessError(1, [])]
            with self.assertRaisesRegex(ValueError, 'could not list local Superset workspaces.*--workspace ID'):
                f.resolve_workspace(self.home)

    def test_interactive_dry_run_uses_agents_create(self):
        prompt = self.home / 'prompt'
        prompt.write_text('hello')
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            f.main(['spawn', '--interactive', '--provider', 'claude', '--role', 'interactive', '--label', 'test',
                    '--workspace', 'ws', '--prompt-file', str(prompt), '--dry-run'])
        data = json.loads(out.getvalue())
        self.assertEqual(data['command'], [f.SUPERSET, 'agents', 'create', '--workspace', 'ws', '--local',
                                          '--agent', 'claude', '--prompt', 'hello', '--json'])
        self.assertEqual(data['supervisor'][1], '_interactive')

    def test_completion_requires_exit_file(self):
        job = f.state_dir() / 'job'
        b.write_json(job / 'meta.json', {'id': 'job', 'state': 'complete'})
        (job / 'result.json').write_text('{"done":true}')
        self.assertFalse(f.inspect_jobs()[0]['complete'])
        (job / 'exit').write_text('7\n')
        collected = f.inspect_jobs(collect=True)[0]
        self.assertEqual(collected['exit'], 7)
        self.assertEqual(collected['result'], '{"done":true}')

    def test_hook_denies_at_zero(self):
        self.reading['windows']['five_hour']['live'] = 70
        result = hook.handle(dict(hook_event_name='PreToolUse', tool_name='Agent', session_id='sid'))
        self.assertEqual(result['hookSpecificOutput']['permissionDecision'], 'deny')
        self.assertIn('bin/fanout', result['hookSpecificOutput']['permissionDecisionReason'])
        self.assertEqual(self.leases(), [])

    def invoke_hook(self, payload):
        out, err = io.StringIO(), io.StringIO()
        with patch.object(sys, 'stdin', io.StringIO(payload)), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(hook.main(), 0)
        return out.getvalue(), err.getvalue()

    def test_hook_fails_open_on_null_leases_filesystem_error_and_bad_stdin(self):
        b.write_json(b.budget_dir() / 'leases.json', None)
        payload = json.dumps(dict(hook_event_name='PreToolUse', tool_name='Agent'))
        out, err = self.invoke_hook(payload)
        self.assertEqual(out, '')
        self.assertIn('allowing tool', err)
        self.assertEqual(len(err.splitlines()), 1)
        with patch.object(b, 'acquire', side_effect=PermissionError('fixture')):
            out, err = self.invoke_hook(payload)
            self.assertEqual(out, '')
            self.assertIn('PermissionError', err)
        with patch.object(hook.json, 'load', side_effect=OSError('stdin failed')):
            out, err = self.invoke_hook(payload)
            self.assertEqual(out, '')
            self.assertIn('allowing tool', err)
        for bad_input in ('not json', 'null'):
            out, err = self.invoke_hook(bad_input)
            self.assertEqual(out, '')
            self.assertIn('allowing tool', err)

    def test_hook_import_failure_also_fails_open(self):
        import builtins
        actual_import = builtins.__import__
        def fail_budget(name, *args, **kwargs):
            if name == 'agent_budget':
                raise ImportError('fixture')
            return actual_import(name, *args, **kwargs)
        with patch.object(hook, 'budget', None), patch.object(builtins, '__import__', side_effect=fail_budget):
            out, err = self.invoke_hook(json.dumps(dict(hook_event_name='PreToolUse', tool_name='Agent')))
        self.assertEqual(out, '')
        self.assertIn('ImportError', err)

    def test_hook_timeout_and_sigterm_allow_without_output(self):
        script = """import importlib.util, os, signal, sys, time
spec = importlib.util.spec_from_file_location('budget_hook', sys.argv[1])
hook = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hook)
hook.HOOK_TIMEOUT_SECONDS = .05
if sys.argv[2] == 'deadline':
    hook.handle = lambda data: time.sleep(5)
else:
    hook.handle = lambda data: os.kill(os.getpid(), signal.SIGTERM)
sys.exit(hook.main())
"""
        payload = json.dumps(dict(hook_event_name='PreToolUse', tool_name='Workflow'))
        for mode in ('deadline', 'sigterm'):
            with self.subTest(mode=mode):
                result = subprocess.run([sys.executable, '-c', script, str(b.ROOT / 'hooks/agent-budget-gate.py'), mode],
                                        input=payload, capture_output=True, text=True, timeout=2)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, '')
                self.assertIn('allowing tool', result.stderr)
                self.assertEqual(len(result.stderr.splitlines()), 1)

    def test_hook_deadline_exits_even_when_slow_probe_catches_exceptions(self):
        script = """import hashlib, importlib.util, sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import agent_budget as b
import claude_quota as q
spec = importlib.util.spec_from_file_location('budget_hook', b.ROOT / 'hooks/agent-budget-gate.py')
hook = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hook)
hook.HOOK_TIMEOUT_SECONDS = .1
state = Path(sys.argv[2])
q.state_dir = lambda: state
q.token = lambda: 'fixture'
b.write_json(state / 'quota-snapshot-cache.json', {
    'identity': hashlib.sha256(b'fixture').hexdigest(), 'observed_at': time.time(),
    'attempted_at': 0, 'source': 'oauth_usage',
    'windows': {'five_hour': {'used_percentage': 99}, 'seven_day': {'used_percentage': 99}}})
def slow_probe(*args, **kwargs):
    (state / 'probe-started').write_text('ready')
    time.sleep(5)
    raise RuntimeError('offline probe finished')
q.urllib.request.urlopen = slow_probe
b.cached_estimates = lambda: b.estimates([], [])
sys.exit(hook.main())
"""
        state = self.home / 'slow-probe'
        payload = json.dumps(dict(hook_event_name='PreToolUse', tool_name='Agent'))
        result = subprocess.run([sys.executable, '-c', script, str(b.ROOT / 'lib'), str(state)],
                                input=payload, capture_output=True, text=True, timeout=2)
        self.assertTrue((state / 'probe-started').exists())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, '')
        self.assertIn('allowing tool', result.stderr)
        self.assertEqual(len(result.stderr.splitlines()), 1)

    def test_estimates_cache_survives_calls_and_expires_after_ten_minutes(self):
        with patch.object(b, 'history', return_value=([], [])) as history:
            first = b.cached_estimates()
            self.assertEqual(b.cached_estimates(), first)
            self.assertEqual(history.call_count, 1)
            cache = b.read_json(b.budget_dir() / 'estimates.json')
            cache['generated_at'] -= 601
            b.write_json(b.budget_dir() / 'estimates.json', cache)
            b.cached_estimates()
            self.assertEqual(history.call_count, 2)
        script = 'import agent_budget as b; b.history=lambda: 1/0; b.cached_estimates()'
        result = subprocess.run([sys.executable, '-c', script], env=dict(os.environ, PYTHONPATH=str(b.ROOT / 'lib')),
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_hook_reuses_models_and_quota_five_second_cache(self):
        self.readings.stop()
        with patch.object(b.claude_quota, 'state_dir', return_value=self.home / 'quota'), patch.object(b.claude_quota, 'token', return_value='fixture'), patch.object(b.claude_quota, 'probe', return_value=({'five_hour': {'used_percentage': 10}, 'seven_day': {'used_percentage': 10}}, None)) as probe, patch.object(b, 'history', return_value=([], [])) as history, patch.object(hook, 'owner_pid', return_value=os.getpid()):
            started = time.monotonic()
            for tool in ('Agent', 'Workflow'):
                self.assertIsNone(hook.handle(dict(hook_event_name='PreToolUse', tool_name=tool, session_id='sid')))
            self.assertLess(time.monotonic() - started, 1)
            self.assertEqual(probe.call_count, 1)
            self.assertEqual(history.call_count, 1)

    def test_hook_unknown_allows_even_with_an_active_child_lease(self):
        b.acquire('claude', 'subagent')
        for window in self.reading['windows'].values():
            window['live'] = None
        with patch.object(b, 'cached_estimates', side_effect=AssertionError('unknown must be cheap')):
            for tool in ('Agent', 'Workflow'):
                self.assertIsNone(hook.handle(dict(hook_event_name='PreToolUse', tool_name=tool)))
        self.assertEqual(b.allow('claude', 'subagent'), 0)  # fanout/batch retain one lease

    def test_hook_releases_exact_subagent_out_of_order(self):
        with patch.object(hook, 'owner_pid', return_value=os.getpid()):
            for tool in ('first', 'second'):
                self.assertIsNone(hook.handle(dict(hook_event_name='PreToolUse', tool_name='Agent', session_id='sid', tool_use_id=tool)))
        path = self.home / 'agent-child.jsonl'
        b.write_json(path.with_suffix('.meta.json'), {'toolUseId': 'second'})
        hook.handle(dict(hook_event_name='SubagentStop', session_id='sid', agent_transcript_path=str(path)))
        self.assertEqual([l['tool_use_id'] for l in self.leases()], ['first'])

    def test_session_hooks_do_not_read_history_or_create_leases(self):
        with patch.object(b, 'history', side_effect=AssertionError('no session accounting')):
            for event in ('SessionStart', 'SessionEnd'):
                self.assertIsNone(hook.handle(dict(hook_event_name=event, session_id='sid')))
        self.assertFalse((b.budget_dir() / 'leases.json').exists())
        # Retire previously installed session leases without affecting children.
        child = b.acquire('claude', 'subagent')
        b.write_json(b.budget_dir() / 'leases.json', [child, dict(child, id='legacy', label='session')])
        self.assertEqual([l['id'] for l in self.leases()], [child['id']])

    def test_headless_supervisor_runs_fake_codex_and_collects_nonzero_exit(self):
        cli_dir = self.home / 'bin'
        cli_dir.mkdir()
        cli = cli_dir / 'codex'
        cli.write_text('#!/usr/bin/env python3\nimport json,os,sys\n'
                       'assert "SUPERSET_AGENT_ID" not in os.environ\n'
                       'print(json.dumps({"type":"turn.completed","usage":{"input_tokens":1000,"cached_input_tokens":800,"output_tokens":10}}))\n'
                       'sys.exit(7)\n')
        cli.chmod(0o755)
        job = f.state_dir() / 'fake'
        b.write_json(job / 'meta.json', dict(id='fake', provider='codex', role='planner', label='fake', interactive=False))
        (job / 'prompt').write_text('hello')
        env = dict(os.environ, PATH=str(cli_dir) + os.pathsep + os.environ['PATH'], SUPERSET_AGENT_ID='parent')
        result = subprocess.run([str(f.FANOUT), '_run', str(job)], env=env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertEqual((job / 'exit').read_text().strip(), '7')
        self.assertEqual(self.leases(), [])
        self.assertEqual(list(b.rows(b.budget_dir() / 'completions.jsonl'))[0]['total'], 210)
        self.assertIn('turn.completed', f.inspect_jobs('fake', collect=True)[0]['result'])

    def test_fanout_accounting_failure_preserves_provider_exit(self):
        job = f.state_dir() / 'failed-accounting'
        b.write_json(job / 'meta.json', dict(id='failed-accounting', provider='codex'))
        (job / 'prompt').write_text('hello')
        with patch.object(f.subprocess, 'Popen') as popen, patch.object(f, 'record_completion', side_effect=OSError()), contextlib.redirect_stderr(io.StringIO()):
            popen.return_value.wait.return_value = 7
            self.assertEqual(f.execute(job), 7)

    def test_interactive_watcher_waits_for_terminal_closure_and_binds_lease(self):
        job = f.state_dir() / 'interactive'
        b.write_json(job / 'meta.json', dict(id='interactive', provider='codex', role='interactive',
                                           label='interactive', workspace='ws'))
        (job / 'prompt').write_text('hello')
        lease = b.acquire('codex', 'interactive', label='interactive')
        responses = [
            dict(kind='terminal', sessionId='terminal'),
            dict(sessions=[dict(terminalId='terminal', exited=False)]),
            dict(sessions=[])]
        with patch.dict(os.environ, AGENT_BUDGET_LEASE_ID=lease['id']), patch.object(b, 'process_start', return_value=lease['pid_started_at']), patch.object(f.time, 'sleep'), patch.object(f.subprocess, 'run') as run:
            run.side_effect = [subprocess.CompletedProcess([], 0, json.dumps(r)) for r in responses]
            self.assertEqual(f.attach(job), 0)
        self.assertEqual(self.leases()[0]['terminal_id'], 'terminal')
        self.assertEqual(b.read_json(job / 'meta.json')['terminal_id'], 'terminal')
        self.assertEqual(run.call_count, 3)

    def test_role_cost_sums_multiple_models_in_one_job(self):
        self.append(b.claude_home() / 'automations/advance-roadmap/runs.jsonl', [
            dict(duration_s=100, tokens=dict(calls=[dict(provider='claude', role='orchestrator', total=v, sessions=['sid']) for v in (10, 20)]))])
        records, _ = b.history()
        self.assertEqual(b.estimates(records, [])['claude']['roles']['planner']['tokens_p80'], 30)
        self.assertEqual(b.estimates(records, [])['claude']['roles']['planner']['ttl'], 150)

    def test_installer_preserves_and_deduplicates_gate_hooks(self):
        import shutil
        if not shutil.which('jq'):
            self.skipTest('jq unavailable')
        settings = self.home / 'settings.json'
        b.write_json(settings, {'keep': 42, 'hooks': {
            'PreToolUse': [{'matcher': 'Bash', 'hooks': [{'command': 'custom'}]}],
            'SessionStart': [{'hooks': [{'command': 'custom'}, {'command': 'python3 agent-budget-gate.py'}]}],
            'SessionEnd': [{'hooks': [{'command': 'python3 agent-budget-gate.py'}]}, {'hooks': [{'command': 'claude-quota.sh'}]}]}})
        source = (b.ROOT / 'install.sh').read_text()
        function = source[source.index('wire_agent_budget_hooks() {'):source.index('# ── hooks/ ──')]
        script = 'backup_settings_once() { :; }\nsettings_target() { echo "$SETTINGS"; }\n' + function + '\nwire_agent_budget_hooks\nwire_agent_budget_hooks\n'
        result = subprocess.run(['bash', '-c', script], capture_output=True, text=True,
            env=dict(os.environ, SRC_ROOT=str(b.ROOT), CLAUDE_DIR=str(self.home), SETTINGS=str(settings)))
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(settings.read_text())
        self.assertEqual(data['keep'], 42)
        self.assertEqual(len(data['hooks']['PreToolUse']), 2)
        self.assertEqual(data['hooks']['PreToolUse'][1]['matcher'], '^(Agent|Workflow)$')
        self.assertEqual(data['hooks']['SessionStart'], [{'hooks': [{'command': 'custom'}]}])
        self.assertEqual(data['hooks']['SessionEnd'], [{'hooks': [{'command': 'claude-quota.sh'}]}])
        self.assertEqual(len(data['hooks']['SubagentStop']), 1)


if __name__ == '__main__':
    unittest.main()
