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

    def test_bundle_is_atomic_and_shares_interactive_reservations(self):
        models = b.estimates([], [])
        readings = {p: self.reading for p in b.PROVIDERS}
        interactive = b.acquire('codex', 'planner')
        requests = [dict(provider='codex', role='planner'), dict(provider='cursor', role='worker')]
        leases = b.reserve_bundle(requests, models=models, reading_by_provider=readings)
        self.assertEqual({l['provider'] for l in leases}, {'codex', 'cursor'})
        self.reading['windows']['five_hour']['live'] = 69
        before = self.leases()
        self.assertIsNone(b.reserve_bundle(requests, replace_ids=[l['id'] for l in leases],
                                          models=models, reading_by_provider=readings))
        self.assertEqual(self.leases(), before, 'failed conversion must preserve both old reservations')
        reduced = b.reserve_bundle([dict(provider='cursor', role='worker', scale=.3)],
                                   replace_ids=[l['id'] for l in leases], models=models,
                                   reading_by_provider=readings, reduce_only=True)
        self.assertEqual(len(reduced), 1)
        self.assertIn(interactive['id'], [l['id'] for l in self.leases()])
        b.release_many([l['id'] for l in reduced])
        self.assertEqual([l['id'] for l in self.leases()], [interactive['id']])

    def test_unknown_bundle_is_one_job_per_provider(self):
        readings = {p: {'windows': {'auto' if p == 'cursor' else 'five_hour': {'live': None, 'cap': 70}}} for p in b.PROVIDERS}
        requests = [dict(provider='cursor', role='worker'), dict(provider='cursor', role='reviewer')]
        lease, = b.reserve_bundle(requests, models=b.estimates([], []), reading_by_provider=readings)
        self.assertEqual(lease['roles'], ['worker', 'reviewer'])
        self.assertIsNone(b.reserve_bundle(requests, models=b.estimates([], []), reading_by_provider=readings))

    def test_read_only_bundle_forecast_never_creates_budget_state(self):
        requests = [dict(provider='codex', role='planner')]
        models = b.cached_estimates(read_only=True)
        self.assertFalse(b.budget_dir().exists())
        preview = b.reserve_bundle(requests, commit=False, models=models,
                                   reading_by_provider={'codex': self.reading})
        self.assertEqual(len(preview), 1)
        self.assertFalse(b.budget_dir().exists())

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
        expired = dict(live, id='expired', created=time.time() - 100, updated=time.time() - 100, ttl=1)
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


class LedgerTests(unittest.TestCase):
    """Token reservations, the settle ledger, fair waiting and nested adoption."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.env = patch.dict(os.environ, {'HOME': str(self.home), 'AGENT_BUDGET_CLAUDE_HOME': str(self.home / '.claude'),
            'ADVANCE_ROADMAP_CODEX_SESSIONS': str(self.home / '.codex/sessions'),
            'ADVANCE_ROADMAP_USAGE_PROBES': '0', 'ANTHROPIC_API_KEY': 'test-disable-oauth',
            'AGENT_BUDGET_WATCH_INTERVAL': '.3'})
        self.env.start()
        self.addCleanup(self.env.stop)
        for name in ('AGENT_BUDGET_LEASE_ID', 'AGENT_BUDGET_LEASES', 'AGENT_BUDGET_SESSION_FILE'):
            os.environ.pop(name, None)
        self.reading = {'source': 'fixture', 'observed_at': time.time(), 'windows': {
            'five_hour': {'live': 50, 'cap': 70}, 'weekly': {'live': 60, 'cap': 80}}}
        patcher = patch.object(b, 'readings', return_value=self.reading)
        patcher.start()
        self.addCleanup(patcher.stop)

    def leases(self):
        with b.lock():
            return b.reap()

    def ledger(self):
        return list(b.rows(b.ledger_path()))

    def cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = b.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def tokens(self, pct, provider='codex'):
        return round(pct / b.cached_estimates()[provider]['factor'])

    def waiter(self, tokens, task, provider='codex'):
        return b.new_waiter(provider, 'worker', tokens, b.tokens_to_pct(provider, tokens), task)

    def write_transcript(self, session_id, outputs, cache_read=500):
        path = b.claude_home() / 'projects/proj' / f'{session_id}.jsonl'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(''.join(json.dumps(dict(type='assistant', uuid=f'u{i}', message=dict(id=f'm{i}', usage={
            'input_tokens': 10, 'cache_creation_input_tokens': 100, 'cache_read_input_tokens': cache_read,
            'output_tokens': out}))) + '\n' for i, out in enumerate(outputs)))

    def test_tokens_convert_to_pct_and_pct_only_leases_still_work(self):
        self.assertAlmostEqual(b.tokens_to_pct('codex', 70000), 3)
        lease = b.acquire('codex', 'planner', tokens=70000, task='t')
        self.assertEqual(lease['reserved_tokens'], 70000)
        self.assertAlmostEqual(lease['pct'], 3)
        self.assertEqual(lease['window_pct'], {'five_hour': lease['pct'], 'weekly': lease['pct']})
        self.assertEqual(lease['agent'], 'codex')
        legacy = dict(id='old', provider='codex', role='worker', pct=3, pid=os.getpid(),
                      pid_started_at=b.process_start(os.getpid()), created=time.time(), ttl=600, label=None)
        b.write_json(b.budget_dir() / 'leases.json', [legacy])
        self.assertAlmostEqual(b.lease_tokens(legacy), 70000)
        self.assertEqual(b.allow('codex', 'planner', 99), 5, 'a pct-only lease still reserves its pct')
        status, revised = b.update_lease('old', add_tokens=10000)
        self.assertEqual(status, 'ok')
        self.assertAlmostEqual(revised['reserved_tokens'], 80000)
        self.assertAlmostEqual(revised['pct'], 80000 * 3 / 70000)
        self.assertEqual(b.settle('old', dict(output=5))[0], 'settled')

    def test_reserve_update_settle_writes_exactly_one_row(self):
        code, out, _ = self.cli('reserve', '--agent', 'codex', '--role', 'worker', '--model', 'gpt-x', '--task', 'review PR #7',
                                '--ticket', 'DEV-7', '--repo', 'ai-tools', '--pid', str(os.getpid()), '--json')
        lease = json.loads(out)
        self.assertEqual((code, lease['linear_id'], lease['repo'], lease['model']), (0, 'DEV-7', 'ai-tools', 'gpt-x'))
        self.assertEqual(lease['reserved_tokens'], b.cached_estimates()['codex']['roles']['worker']['tokens_p80'])
        code, out, _ = self.cli('update', '--lease', lease['id'], '--tokens', '100000', '--session-id', 's1', '--json')
        self.assertEqual((code, json.loads(out)['reserved_tokens']), (0, 100000))
        code, out, _ = self.cli('update', '--lease', lease['id'], '--add-tokens', '5000', '--json')
        self.assertEqual(json.loads(out)['reserved_tokens'], 105000)
        code, out, _ = self.cli('settle', '--lease', lease['id'], '--input', '70000', '--cache-write', '1000',
                                '--cache-read', '900000', '--output', '4000', '--session-id', 's2', '--outcome', 'ok', '--json')
        row = json.loads(out)
        self.assertEqual((code, row['status']), (0, 'settled'))
        self.assertEqual(row['actual'], dict(input=70000, cache_write=1000, cache_read=900000, output=4000))
        self.assertEqual(row['actual_total'], 75000, 'cache reads are tracked apart from new tokens')
        self.assertEqual((row['agent'], row['model'], row['role'], row['task'], row['linear_id'], row['repo']),
                         ('codex', 'gpt-x', 'worker', 'review PR #7', 'DEV-7', 'ai-tools'))
        self.assertEqual((row['session_ids'], row['reserved_tokens'], row['outcome'], row['over_run']),
                         (['s1', 's2'], 105000, 'ok', False))
        self.assertEqual(row['window_before'], {'five_hour': 50, 'weekly': 60})
        self.assertEqual(row['window_after'], {'five_hour': 50, 'weekly': 60})
        self.assertIsInstance(row['duration_s'], float)
        self.assertEqual(self.leases(), [])
        code, out, _ = self.cli('settle', '--lease', lease['id'], '--output', '1', '--json')
        self.assertEqual((code, json.loads(out)['status']), (0, 'already_settled'))
        self.assertEqual(len(self.ledger()), 1)
        self.assertEqual(self.cli('settle', '--lease', 'nope', '--output', '1')[0], b.EXIT_NO_LEASE)

    def test_settle_reports_over_run_and_records_failed_outcomes(self):
        lease = b.acquire('codex', 'worker', tokens=1000, task='t')
        _, row = b.settle(lease['id'], dict(input=900, output=300), outcome='limit')
        self.assertEqual((row['over_run'], row['outcome'], row['actual_total']), (True, 'limit', 1200))
        with self.assertRaises(ValueError):
            b.settle('x', outcome='fine')

    def test_late_settle_after_the_lease_was_reaped_still_logs_one_row(self):
        lease = b.acquire('codex', 'worker', tokens=1000, task='t')
        stored = b.read_json(b.budget_dir() / 'leases.json')
        stored[0].update(created=time.time() - 100, updated=time.time() - 100, ttl=1)
        b.write_json(b.budget_dir() / 'leases.json', stored)
        self.assertEqual(self.leases(), [])
        self.assertEqual(len(b.read_json(b.budget_dir() / 'reaped.json')), 1)
        self.assertEqual(b.settle(lease['id'], dict(output=500))[0], 'settled')
        self.assertEqual(b.settle(lease['id'], dict(output=500))[0], 'already_settled')
        self.assertEqual([r['actual_total'] for r in self.ledger()], [500])
        self.assertEqual(b.read_json(b.budget_dir() / 'reaped.json'), [])

    def test_cli_reservation_outlives_the_cli_process(self):
        script = '''import sys
sys.path.insert(0, sys.argv[1])
import agent_budget as b
b.readings = lambda p: {'windows': {'five_hour': {'live': 1, 'cap': 70}}}
sys.exit(b.main(['reserve', '--agent', 'claude', '--role', 'subagent', '--task', 't']))
'''
        result = subprocess.run([sys.executable, '-c', script, str(b.ROOT / 'lib')], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        lease, = self.leases()
        self.assertEqual(lease['id'], result.stdout.strip())
        self.assertEqual(lease['pid'], os.getpid(), 'owned by the caller, not the exited CLI')

    def test_update_that_does_not_fit_is_refused_but_never_kills_anything(self):
        lease = b.acquire('codex', 'worker', tokens=70000, task='t')
        code, out, err = self.cli('update', '--lease', lease['id'], '--tokens', str(self.tokens(30)), '--json')
        self.assertEqual(code, b.EXIT_WOULD_EXCEED)
        self.assertEqual(json.loads(out)['status'], 'would_exceed')
        self.assertIn('nothing was stopped', err)
        self.assertEqual(self.leases()[0]['reserved_tokens'], 70000)
        code, out, _ = self.cli('update', '--lease', lease['id'], '--tokens', str(self.tokens(30)), '--force', '--json')
        self.assertEqual((code, json.loads(out)['status']), (0, 'over_run'))
        self.assertTrue(self.leases()[0]['over_run'])
        self.assertTrue(b.settle(lease['id'], dict(output=1))[1]['over_run'])
        self.assertEqual(self.cli('update', '--lease', 'gone', '--tokens', '1')[0], b.EXIT_NO_LEASE)
        again = b.acquire('codex', 'worker', tokens=70000, task='t')
        self.assertEqual(b.update_lease(again['id'], tokens=1000)[0], 'ok', 'shrinking always fits')

    def test_interactive_reservation_never_blocks(self):
        self.reading['windows']['five_hour']['live'] = 70
        blocked = self.cli('reserve', '--agent', 'codex', '--role', 'worker', '--task', 't', '--pid', str(os.getpid()))
        self.assertEqual(blocked[0], b.EXIT_NO_HEADROOM)
        code, out, err = self.cli('reserve', '--agent', 'codex', '--role', 'worker', '--task', 'session', '--interactive',
                                  '--pid', str(os.getpid()), '--json')
        lease = json.loads(out)
        self.assertEqual(code, 0)
        self.assertTrue(lease['interactive'] and lease['over_capacity'])
        self.assertEqual(err.count('\n'), 1, 'one warning line')
        self.assertIn('headroom is short', err)
        self.assertEqual([l['id'] for l in self.leases()], [lease['id']])
        self.assertEqual(b.read_json(b.waiters_path(), []), [], 'interactive reservations never queue')
        with patch.object(b, 'acquire', side_effect=OSError('disk full')):
            code, out, err = self.cli('reserve', '--agent', 'codex', '--task', 't', '--interactive')
        self.assertEqual((code, out), (0, ''))
        self.assertIn('session continues', err)

    def test_waiter_admits_when_a_reservation_clears(self):
        hog = b.acquire('codex', 'worker', tokens=self.tokens(18), task='hog')
        releases = []

        def clear(_seconds, _received):
            releases.append(b.settle(hog['id'], dict(output=10))[0])

        with patch.object(b, 'pause', clear), contextlib.redirect_stderr(io.StringIO()):
            lease = b.admit('codex', 'worker', wait=True, tokens=self.tokens(5), task='waiter')
        self.assertEqual(releases, ['settled'])
        self.assertEqual(lease['task'], 'waiter')
        self.assertEqual(b.read_json(b.waiters_path()), [])

    def test_queue_is_fifo_and_bounded_bypass_protects_the_head(self):
        blocker = b.acquire('codex', 'worker', tokens=self.tokens(15), task='blocker')   # headroom left: 5
        head = self.waiter(self.tokens(10), 'big')
        self.assertIsNone(b.acquire('codex', 'worker', tokens=head['tokens'], waiter=head))
        for i in range(b.MAX_BYPASS):
            small = self.waiter(self.tokens(.5), f'small{i}')
            self.assertIsNotNone(b.acquire('codex', 'worker', tokens=small['tokens'], waiter=small, task=small['task']),
                                 'a job that fits is not starved by the head')
        self.assertEqual(b.read_json(b.waiters_path())[0]['bypassed'], b.MAX_BYPASS)
        late = self.waiter(self.tokens(.5), 'late')
        self.assertIsNone(b.acquire('codex', 'worker', tokens=late['tokens'], waiter=late),
                          'after the bypass budget the head is reserved for')
        for lease in self.leases():
            b.release(lease['id'])
        self.assertIsNotNone(b.acquire('codex', 'worker', tokens=head['tokens'], waiter=head), 'the head admits once drained')
        self.assertEqual([w['task'] for w in b.read_json(b.waiters_path())], ['late'])
        self.assertIsNotNone(blocker)

    def test_earlier_waiter_that_fits_goes_first(self):
        first, second = self.waiter(self.tokens(2), 'first'), self.waiter(self.tokens(2), 'second')
        self.reading['windows']['five_hour']['live'] = 70
        self.assertIsNone(b.acquire('codex', 'worker', tokens=first['tokens'], waiter=first))
        self.assertIsNone(b.acquire('codex', 'worker', tokens=second['tokens'], waiter=second))
        self.reading['windows']['five_hour']['live'] = 50
        self.assertIsNone(b.acquire('codex', 'worker', tokens=second['tokens'], waiter=second),
                          'second may not overtake a first that fits')
        self.assertIsNotNone(b.acquire('codex', 'worker', tokens=first['tokens'], waiter=first))
        self.assertIsNotNone(b.acquire('codex', 'worker', tokens=second['tokens'], waiter=second))

    def test_a_head_blocked_by_live_usage_never_starves_smaller_jobs(self):
        huge = self.waiter(self.tokens(30), 'huge')
        self.assertIsNone(b.acquire('codex', 'worker', tokens=huge['tokens'], waiter=huge))
        for i in range(b.MAX_BYPASS + 3):
            small = self.waiter(self.tokens(.5), f's{i}')
            self.assertIsNotNone(b.acquire('codex', 'worker', tokens=small['tokens'], waiter=small))

    def test_dead_waiters_leave_the_queue_and_status_shows_waiting(self):
        dead = b.new_waiter('codex', 'worker', 1000, 1, 'ghost')
        dead.update(pid=99999999)
        live = b.new_waiter('codex', 'worker', 5000, 2, 'patient')
        b.write_json(b.waiters_path(), [dead, live])
        code, out, _ = self.cli('status', '--json')
        waiting = json.loads(out)['codex']['waiting']
        self.assertEqual([w['task'] for w in waiting], ['patient'])
        self.assertEqual(waiting[0]['tokens'], 5000)
        _, text, _ = self.cli('status')
        self.assertIn('waiting: patient needs 5000 tokens', text)

    def test_run_logs_one_row_with_ticket_task_and_outcome(self):
        code = b.run('codex', 'worker', [sys.executable, '-c', 'pass'], task='review one PR', ticket='DEV-9', repo='r', kind='review')
        self.assertEqual(code, 0)
        row, = self.ledger()
        self.assertEqual((row['task'], row['linear_id'], row['repo'], row['kind'], row['outcome'], row['actual_source']),
                         ('review one PR', 'DEV-9', 'r', 'review', 'ok', 'none'))
        self.assertEqual(self.leases(), [])
        self.assertEqual(b.run('codex', 'worker', [sys.executable, '-c', 'import sys; sys.exit(7)']), 7)
        self.assertEqual([r['outcome'] for r in self.ledger()], ['ok', 'error'])
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(b.run('codex', 'worker', ['/no/such/cli']), 127)
        self.assertEqual(len(self.ledger()), 2, 'a command that never ran logs no completed output')
        self.assertEqual(self.leases(), [])

    def test_run_discovers_actual_tokens_from_session_files(self):
        self.write_transcript('claude-sid', [200, 300])
        session_out = self.home / 'session'
        session_out.write_text('claude-sid\n')
        self.assertEqual(b.run('claude', 'worker', [sys.executable, '-c', 'pass'], session_out=str(session_out)), 0)
        row, = self.ledger()
        self.assertEqual(row['actual'], dict(input=20, cache_write=200, cache_read=1000, output=500))
        self.assertEqual((row['session_ids'], row['actual_source']), (['claude-sid'], 'sessions'))

    def test_run_reads_codex_rollouts_from_the_stdout_header_and_totals_from_the_result_file(self):
        sid = '5f1d2c3e-aaaa-bbbb-cccc-0123456789ab'
        rollout = self.home / f'.codex/sessions/2026/10/29/rollout-2026-10-29T10-00-00-{sid}.jsonl'
        rollout.parent.mkdir(parents=True)
        rollout.write_text(json.dumps(dict(payload=dict(type='token_count', info=dict(total_token_usage=dict(
            input_tokens=1000, cached_input_tokens=600, output_tokens=50))))) + '\n')
        result = self.home / 'codex-out'
        result.write_text(f'session id: {sid}\nworking\ntokens used\n1,234\n')
        self.assertEqual(b.run('codex', 'worker', [sys.executable, '-c', 'pass'], result_file=str(result)), 0)
        row = self.ledger()[-1]
        self.assertEqual(row['actual'], dict(input=400, cache_write=0, cache_read=600, output=50), 'the rollout is the truth')
        self.assertEqual((row['session_ids'], row['actual_source']), ([sid], 'sessions'))
        total_only = self.home / 'total-only'
        total_only.write_text('working\ntokens used\n1,234\n')
        self.assertEqual(b.run('codex', 'worker', [sys.executable, '-c', 'pass'], result_file=str(total_only)), 0)
        self.assertEqual((self.ledger()[-1]['actual_total'], self.ledger()[-1]['actual_source']), (1234, 'result'))
        events = self.home / 'events'
        events.write_text('\n'.join(json.dumps(e) for e in [
            dict(type='thread.started', thread_id='t-1'),
            dict(type='turn.completed', usage=dict(input_tokens=100, cached_input_tokens=40, output_tokens=7)),
            dict(type='turn.completed', usage=dict(input_tokens=50, cached_input_tokens=0, output_tokens=3))]))
        self.assertEqual(b.result_usage('codex', events), (dict(input=110, cache_write=0, cache_read=40, output=10), 't-1'))

    def test_run_falls_back_to_the_single_new_transcript_in_its_cwd(self):
        slug = ''.join(c if c.isalnum() else '-' for c in os.getcwd())
        path = b.claude_home() / 'projects' / slug / 'found.jsonl'
        path.parent.mkdir(parents=True)
        script = f'''import json, time, pathlib
pathlib.Path({str(path)!r}).write_text(json.dumps(dict(type='assistant', uuid='u', timestamp=time.time(),
    message=dict(id='m', usage=dict(input_tokens=5, output_tokens=7)))) + chr(10))'''
        self.assertEqual(b.run('claude', 'worker', [sys.executable, '-c', script]), 0)
        row, = self.ledger()
        self.assertEqual((row['session_ids'], row['actual_total'], row['actual_source']), (['found'], 12, 'discovered'))
        (path.parent / 'second.jsonl').write_text(path.read_text())
        self.assertEqual(b.discover_sessions('claude', os.getcwd(), 0), [], 'two candidates are ambiguous')

    def watch_run(self, live, child_code=0):
        self.reading['windows']['five_hour']['live'] = live
        self.write_transcript('watched', [45000, 45000])    # 90000 output + 200 cache write + 20 input = 90220 new tokens
        session_out = self.home / 'watched-session'
        session_out.write_text('watched')
        done = self.home / 'done'
        script = f'import time, pathlib, sys; time.sleep(1.2); pathlib.Path({str(done)!r}).write_text("x"); sys.exit({child_code})'
        code = b.run('claude', 'worker', [sys.executable, '-c', script], tokens=100000, session_out=str(session_out))
        return code, done.exists(), self.ledger()[-1]

    def test_watch_extends_the_reservation_without_interrupting_the_child(self):
        code, finished, row = self.watch_run(live=50)
        self.assertEqual((code, finished), (0, True))
        self.assertEqual((row['initial_tokens'], row['actual_total']), (100000, 90220))
        self.assertEqual(row['reserved_tokens'], 150000, 'extended by half again once 80% was used')
        self.assertEqual((row['over_run'], row['outcome']), (False, 'ok'))

    def test_watch_records_an_over_run_when_the_extension_does_not_fit(self):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code, finished, row = self.watch_run(live=65.5, child_code=3)
        self.assertEqual((code, finished), (3, True), 'the child ran to completion with its own exit status')
        self.assertTrue(row['over_run'])
        self.assertEqual(row['reserved_tokens'], 150000, 'the tokens were being spent, so other admitters must see them')
        self.assertEqual(row['outcome'], 'error')
        self.assertIn('recorded as an over-run, job left running', stderr.getvalue())

    def test_nested_run_adopts_the_parents_lease(self):
        parent = b.acquire('codex', 'worker', tokens=1000, task='parent task', linear_id='DEV-1')
        marker = self.home / 'env'
        session_out = self.home / 'nested-session'
        session_out.write_text('nested-sid\n')
        script = f'import os, pathlib; pathlib.Path({str(marker)!r}).write_text(os.environ["AGENT_BUDGET_LEASE_ID"])'
        with patch.dict(os.environ, {'AGENT_BUDGET_LEASE_ID': parent['id']}):
            code = b.run('codex', 'reviewer', [sys.executable, '-c', script], tokens=5000, task='nested',
                         ticket='DEV-2', session_out=str(session_out))
            code2, out, _ = self.cli('reserve', '--agent', 'codex', '--task', 'again', '--tokens', '2000', '--json')
        self.assertEqual((code, code2), (0, 0))
        self.assertEqual(marker.read_text(), parent['id'])
        lease, = self.leases()
        self.assertEqual((lease['id'], json.loads(out)['id'], json.loads(out)['adopted']), (parent['id'], parent['id'], True))
        self.assertEqual((lease['reserved_tokens'], lease['session_ids'], lease['task'], lease['linear_id']),
                         (5000, ['nested-sid'], 'parent task', 'DEV-1'))
        self.assertEqual(self.ledger(), [], 'only the owner settles')
        self.assertEqual(b.settle(parent['id'])[1]['session_ids'], ['nested-sid'])

    def test_nested_run_for_another_agent_takes_its_own_lease(self):
        parent = b.acquire('cursor', 'worker', tokens=1000, task='parent')
        with patch.dict(os.environ, {'AGENT_BUDGET_LEASE_ID': parent['id'], 'AGENT_BUDGET_LEASES': 'cursor=' + parent['id']}):
            self.assertIsNone(b.parent_lease('codex'))
            self.assertEqual(b.run('codex', 'reviewer', [sys.executable, '-c', 'pass'], task='review'), 0)
        row, = self.ledger()
        self.assertEqual((row['agent'], row['parent_lease_id']), ('codex', parent['id']))
        self.assertEqual([l['id'] for l in self.leases()], [parent['id']])
        own = b.acquire('codex', 'reviewer', tokens=1000, task='own')
        env = b.child_lease_env(own, {'AGENT_BUDGET_LEASES': 'cursor=' + parent['id']})
        self.assertEqual(env['AGENT_BUDGET_LEASES'], f'cursor={parent["id"]},codex={own["id"]}')
        with patch.dict(os.environ, env):
            self.assertEqual(b.parent_lease('codex')['id'], own['id'])
            self.assertEqual(b.parent_lease('cursor')['id'], parent['id'])

    def row(self, agent='codex', role='worker', total=1000, kind=None, outcome='ok', **extra):
        return dict(lease_id=extra.pop('lease_id', os.urandom(4).hex()), agent=agent, role=role, kind=kind or role,
                    outcome=outcome, actual=dict(input=total, cache_write=0, cache_read=999999, output=0), duration_s=60, **extra)

    def test_estimates_prefer_enough_ledger_rows_per_bucket(self):
        history = [{'provider': 'codex', 'role': 'worker', 'total': 5000}] * 10
        two = b.ledger_records([self.row(total=100), self.row(total=200)])
        self.assertEqual(b.estimates(history + two, [])['codex']['roles']['worker']['tokens_p80'], 5000,
                         'two rows are not enough to displace the older sources, and are never pooled with them')
        rows = b.ledger_records([self.row(total=t) for t in (100, 200, 300)] + [
            self.row(total=9, outcome='error'), self.row(total=999999, outcome='cancelled')])
        model = b.estimates(history + rows, [])['codex']['roles']['worker']
        self.assertEqual((model['tokens_p80'], model['source'], model['observations']), (300, 'ledger', 3))
        self.assertEqual(b.estimates(history, [])['codex']['roles']['worker']['source'], 'history')
        self.assertEqual(b.estimates(rows, [])['claude']['roles']['worker']['source'], 'seed')
        self.assertEqual(b.estimates(two, [])['codex']['roles']['worker']['source'], 'ledger', 'a thin ledger beats no history')
        reviews = b.ledger_records([self.row(kind='review', total=t) for t in (10, 20, 30)] + [self.row(total=500)])
        models = b.estimates(reviews, [])
        self.assertEqual(models['codex']['roles']['worker']['kinds'], {'review': {'tokens_p80': 30, 'observations': 3}})
        self.assertEqual(b.resolve_cost('codex', 'worker', None, 'review', models)[0], 30)
        self.assertEqual(b.resolve_cost('codex', 'worker', None, 'other', models)[0],
                         models['codex']['roles']['worker']['tokens_p80'])

    def test_history_reads_the_ledger_and_calibrates_from_window_pairs(self):
        now = time.time()

        def paired(lease_id, before, after, observed=60):
            return self.row(total=10000, lease_id=lease_id, started_at=now - 60, at=now, observed_before=now - observed,
                            observed_after=now, window_before={'five_hour': before}, window_after={'five_hour': after})

        rows = [paired(f'l{i}', 10, 11) for i in range(5)] + [paired('stale', 10, 30, observed=9999), paired('back', 30, 10)]
        b.budget_dir().mkdir(parents=True)
        b.ledger_path().write_text(''.join(json.dumps(r) + '\n' for r in rows))
        records, samples = b.history(persist=True)
        self.assertEqual(len([r for r in records if r.get('source') == 'ledger']), 7)
        self.assertEqual(sorted(s['id'] for s in samples if s['source'] == 'ledger'), [f'ledger:l{i}' for i in range(5)])
        model = b.estimates(records, samples)['codex']
        self.assertEqual(model['factor_source'], 'calibrated')
        self.assertAlmostEqual(model['factor'], 1 / 10000)
        b.ledger_path().write_text('')
        self.assertEqual([s for s in b.history(persist=True)[1] if s['source'] == 'ledger'], [], 'generated samples are rebuilt')

    def test_settle_reads_result_json_files(self):
        lease = b.acquire('claude', 'worker', tokens=1000, task='t')
        result = self.home / 'result.json'
        result.write_text(json.dumps(dict(result='done', session_id='sid-r', modelUsage={
            'claude-sonnet-5-5': dict(inputTokens=10, cacheCreationInputTokens=20, cacheReadInputTokens=30, outputTokens=40),
            'claude-opus-5-5': dict(inputTokens=1, outputTokens=2)})) + '\n')
        _, out, _ = self.cli('settle', '--lease', lease['id'], '--actual-json', str(result), '--json')
        self.assertEqual(json.loads(out)['actual'], dict(input=11, cache_write=20, cache_read=30, output=42))
        other = b.acquire('claude', 'worker', tokens=1000, task='t')
        flat = self.home / 'flat.json'
        flat.write_text(json.dumps(dict(usage=dict(input_tokens=3, output_tokens=4))))
        _, out, _ = self.cli('settle', '--lease', other['id'], '--actual-json', str(flat), '--json')
        self.assertEqual(json.loads(out)['actual_total'], 7)
        empty = self.home / 'empty.json'
        empty.write_text('{}')
        third = b.acquire('claude', 'worker', tokens=1000, task='t')
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            self.cli('settle', '--lease', third['id'], '--actual-json', str(empty))
        self.assertEqual([l['id'] for l in self.leases()], [third['id']], 'a bad usage file settles nothing')

    def test_interactive_run_fails_open_when_the_ledger_is_broken(self):
        marker = self.home / 'ran'
        script = f'import pathlib, sys; pathlib.Path({str(marker)!r}).write_text("x"); sys.exit(5)'
        for error in (OSError('read-only'), PermissionError('lock'), subprocess.TimeoutExpired('ps', 1)):
            marker.unlink(missing_ok=True)
            stderr = io.StringIO()
            with patch.object(b, 'acquire', side_effect=error), contextlib.redirect_stderr(stderr):
                code = b.run('claude', 'interactive', [sys.executable, '-c', script], interactive=True)
            self.assertEqual((code, marker.exists()), (5, True), 'the tool still starts and keeps its own exit status')
            self.assertIn('session continues unreserved', stderr.getvalue())
        with patch.object(b, 'acquire', side_effect=OSError('read-only')), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(b.run('claude', 'worker', [sys.executable, '-c', 'pass']), 127, 'headless still reports it')

    def test_interactive_run_keeps_the_terminal_and_ignores_ctrl_c(self):
        import threading
        groups = self.home / 'groups'
        script = f'import os, pathlib, time; pathlib.Path({str(groups)!r}).write_text(str(os.getpgid(0))); time.sleep(.8)'
        threading.Timer(.4, os.kill, (os.getpid(), signal.SIGINT)).start()
        self.assertEqual(b.run('claude', 'interactive', [sys.executable, '-c', script], interactive=True), 0)
        self.assertEqual(int(groups.read_text()), os.getpgid(0), 'a TUI child stays in the terminal\'s process group')
        row, = self.ledger()
        self.assertEqual((row['outcome'], row['interactive']), ('ok', True), 'Ctrl-C is not a cancellation')
        self.assertEqual(self.leases(), [])
        b.run('claude', 'worker', [sys.executable, '-c', script])
        self.assertNotEqual(int(groups.read_text()), os.getpgid(0), 'headless children get their own session')

    def test_waiters_keep_their_place_however_long_they_wait(self):
        old = self.waiter(1000, 'patient')
        old['arrived'] -= 13 * 3600
        b.write_json(b.waiters_path(), [old])
        self.assertEqual([w['task'] for w in b.reap_waiters()], ['patient'])
        later = self.waiter(self.tokens(.5), 'later')
        self.reading['windows']['five_hour']['live'] = 70
        self.assertIsNone(b.acquire('codex', 'worker', tokens=later['tokens'], waiter=later))
        self.assertEqual([w['task'] for w in b.read_json(b.waiters_path())], ['patient', 'later'])

    def test_owner_pid_walks_past_shells_but_never_returns_init(self):
        def ps(table):
            def fake(argv, **_kw):
                ppid, comm = table[int(argv[-1])]
                return subprocess.CompletedProcess(argv, 0, stdout=f'{ppid} {comm}\n')
            return fake
        # direct parent -> `$(...)` subshell -> script zsh -> the real owner
        with patch.object(b.os, 'getppid', return_value=300), patch.object(
                b.subprocess, 'run', ps({300: (200, '/bin/zsh'), 200: (100, 'zsh'), 100: (7, '/usr/bin/claude')})):
            self.assertEqual(b.owner_pid(), 100)
        # a script launched by launchd: stop at its shell, never at pid 1
        with patch.object(b.os, 'getppid', return_value=300), patch.object(
                b.subprocess, 'run', ps({300: (200, 'zsh'), 200: (1, 'zsh')})):
            self.assertEqual(b.owner_pid(), 200)

    def test_claude_actual_takes_the_larger_of_transcript_and_result_model_usage(self):
        self.write_transcript('adv', [100])    # 10 input, 100 cache write, 100 output
        result = self.home / 'adv.json'
        result.write_text(json.dumps(dict(result='x', session_id='adv', modelUsage={
            'sonnet': dict(inputTokens=10, cacheCreationInputTokens=100, cacheReadInputTokens=5, outputTokens=100),
            'opus-advisor': dict(inputTokens=4000, outputTokens=900)})) + '\n')
        usage, source, found = b.usage_for_sessions('claude', ['adv'], str(result))
        self.assertEqual((usage, source, found), (dict(input=4010, cache_write=100, cache_read=500, output=1000), 'sessions', ['adv']))
        lease = b.acquire('claude', 'worker', tokens=1000, task='t')
        row = b.settle(lease['id'], session_ids=['adv'], result_file=str(result))[1]
        self.assertEqual(row['actual_total'], 4010 + 100 + 1000)

    def test_hook_and_batch_leases_still_work_and_carry_ledger_fields(self):
        lease = b.acquire('claude', 'subagent', pid=os.getpid(), label='hook', session_id='s', tool_use_id='t', unknown_limit=False)
        self.assertEqual((lease['label'], lease['session_id'], lease['reserved_tokens'] > 0, lease['agent']),
                         ('hook', 's', True, 'claude'))
        bundle = b.reserve_bundle([dict(provider='codex', role='planner')], reading_by_provider={'codex': self.reading})
        self.assertIsNotNone(bundle)
        self.assertAlmostEqual(self.leases()[-1]['pct'], bundle[0]['pct'])


if __name__ == '__main__':
    unittest.main()
