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

    def test_hook_denies_at_zero_and_takes_no_lease(self):
        with patch.object(b, 'allow', return_value=0), patch.object(b, 'acquire') as acquire:
            result = hook.handle(dict(hook_event_name='PreToolUse', tool_name='Agent', session_id='sid'))
        self.assertEqual(result['hookSpecificOutput']['permissionDecision'], 'deny')
        self.assertIn('bin/fanout', result['hookSpecificOutput']['permissionDecisionReason'])
        acquire.assert_not_called()

    def test_hook_releases_exact_subagent_out_of_order(self):
        with patch.object(hook, 'owner_pid', return_value=os.getpid()):
            for tool in ('first', 'second'):
                self.assertIsNone(hook.handle(dict(hook_event_name='PreToolUse', tool_name='Agent', session_id='sid', tool_use_id=tool)))
        path = self.home / 'agent-child.jsonl'
        b.write_json(path.with_suffix('.meta.json'), {'toolUseId': 'second'})
        hook.handle(dict(hook_event_name='SubagentStop', session_id='sid', agent_transcript_path=str(path)))
        self.assertEqual([l['tool_use_id'] for l in self.leases()], ['first'])

    def test_conducting_session_reservation_and_lifecycle(self):
        with patch.object(hook, 'owner_pid', return_value=os.getpid()):
            for _ in range(2):
                hook.handle(dict(hook_event_name='SessionStart', session_id='sid'))
        self.assertEqual(len(self.leases()), 1)
        self.assertAlmostEqual(self.leases()[0]['pct'], 10)
        hook.handle(dict(hook_event_name='SessionEnd', session_id='sid'))
        self.assertEqual(self.leases(), [])

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
        with patch.dict(os.environ, AGENT_BUDGET_LEASE_ID=lease['id']), patch.object(f.time, 'sleep'), patch.object(f.subprocess, 'run') as run:
            run.side_effect = [subprocess.CompletedProcess([], 0, json.dumps(r)) for r in responses]
            self.assertEqual(f.attach(job), 0)
        self.assertEqual(self.leases()[0]['terminal_id'], 'terminal')
        self.assertEqual(b.read_json(job / 'meta.json')['terminal_id'], 'terminal')
        self.assertEqual(run.call_count, 3)

    def test_adopted_interactive_session_is_not_double_counted(self):
        lease = b.acquire('claude', 'interactive', label='fanout')
        lease['terminal_id'] = 'terminal'
        b.write_json(b.budget_dir() / 'leases.json', [lease])
        with patch.dict(os.environ, SUPERSET_TERMINAL_ID='terminal'):
            hook.handle(dict(hook_event_name='SessionStart', session_id='sid'))
            hook.handle(dict(hook_event_name='SessionEnd', session_id='sid'))
        self.assertEqual(len(self.leases()), 1)
        self.assertEqual(self.leases()[0]['id'], lease['id'])

    def test_headless_session_adopts_wrapper_lease(self):
        lease = b.acquire('claude', 'planner', label='fanout')
        with patch.dict(os.environ, AGENT_BUDGET_LEASE_ID=lease['id']):
            hook.handle(dict(hook_event_name='SessionStart', session_id='sid'))
            hook.handle(dict(hook_event_name='SessionEnd', session_id='sid'))
        self.assertEqual([l['id'] for l in self.leases()], [lease['id']])

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
        b.write_json(settings, {'keep': 42, 'hooks': {'PreToolUse': [{'matcher': 'Bash', 'hooks': [{'command': 'custom'}]}]}})
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
        for event in ('SessionStart', 'SessionEnd', 'SubagentStop'):
            self.assertEqual(len(data['hooks'][event]), 1)


if __name__ == '__main__':
    unittest.main()
