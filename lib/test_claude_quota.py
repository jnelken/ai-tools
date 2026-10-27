import contextlib
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import claude_quota as q


class QuotaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name)
        self.patch = patch.object(q, 'state_dir', return_value=self.state)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        auth = patch.object(q, 'token', return_value='test-credential')
        auth.start()
        self.addCleanup(auth.stop)

    def test_every_boundary_logs_with_shared_probe_and_no_secrets(self):
        with patch.object(q, 'probe', return_value=({'five_hour': {'used_percentage': 72, 'resets_at': 'later'}}, None)) as probe:
            q.snapshot('start', launch_id='same')
            q.snapshot('finish', launch_id='same', exit_code=1)
        rows = [json.loads(line) for line in (self.state / 'quota-snapshots.jsonl').read_text().splitlines()]
        self.assertEqual(probe.call_count, 1)
        self.assertEqual([r['event'] for r in rows], ['start', 'finish'])
        self.assertEqual(rows[1]['quota']['windows']['five_hour']['used_percentage'], 72)
        self.assertEqual(rows[0]['quota']['status'], 'fresh')
        self.assertEqual((self.state / 'quota-snapshots.jsonl').stat().st_mode & 0o777, 0o600)

    def test_unavailable_is_not_zero(self):
        with patch.object(q, 'probe', return_value=(None, 'http_429')):
            data = q.quota()
        self.assertEqual(data['status'], 'unavailable')
        self.assertEqual(data['windows'], {})
        self.assertEqual(data['error'], 'http_429')

    def test_old_statusline_is_explicitly_stale(self):
        (self.state / 'claude-usage.json').write_text(json.dumps({
            'timestamp': time.time() - 3600,
            'five_hour': {'used_percentage': 90, 'reset_epoch': 2000000000}}))
        with patch.object(q, 'probe', return_value=(None, 'no_subscription_credentials')):
            data = q.quota()
        self.assertEqual(data['status'], 'stale')
        self.assertGreater(data['age_seconds'], 3599)

    def test_supervisor_preserves_exit_and_does_not_inherit_duplicate_hooks(self):
        with patch.object(q, 'snapshot') as record:
            code = q.run([sys.executable, '-c', 'import os,sys; sys.exit(7 if os.environ.get("CLAUDE_QUOTA_MANAGED") else 8)'])
        self.assertEqual(code, 7)
        self.assertEqual([c.args[0] for c in record.call_args_list], ['start', 'finish'])
        self.assertEqual(record.call_args_list[1].kwargs['exit_code'], 7)

    def test_spawn_failure_and_telemetry_failure_do_not_mask_cli(self):
        with patch.object(q, 'snapshot') as record, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(q.run(['/not/a/cli']), 127)
        self.assertEqual(record.call_args_list[-1].args[0], 'finish')
        with patch.object(q, 'quota', side_effect=OSError()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(q.run([sys.executable, '-c', 'pass']), 0)

    def test_timeout_signal_is_forwarded_and_finish_is_recorded(self):
        script = '''import sys, signal, threading, os
sys.path.insert(0, sys.argv[1])
import claude_quota as q
q.snapshot = lambda event, **kw: print(event, kw.get('exit_code'), flush=True)
threading.Timer(.5, lambda: os.kill(os.getpid(), signal.SIGTERM)).start()
sys.exit(q.run([sys.executable, '-c', 'import time; time.sleep(30)']))
'''
        result = subprocess.run([sys.executable, '-c', script, str(Path(q.__file__).parent)], capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 143)
        self.assertIn('finish -15', result.stdout)

    def test_hook_suppression_does_not_read_stdin(self):
        with patch.dict(os.environ, CLAUDE_QUOTA_MANAGED='outer'), patch.object(sys, 'argv', ['claude-session', 'hook']), patch.object(q, 'snapshot') as record:
            self.assertEqual(q.main(), 0)
        record.assert_not_called()

    def test_hook_only_logs_whitelisted_context(self):
        payload = dict(hook_event_name='SessionStart', session_id='sid', prompt='SECRET')
        with patch.dict(os.environ, {}, clear=True), patch.object(sys, 'argv', ['claude-session', 'hook']), patch.object(sys, 'stdin', io.StringIO(json.dumps(payload))), patch.object(q, 'snapshot') as record:
            self.assertEqual(q.main(), 0)
        self.assertEqual(record.call_args.kwargs['session_id'], 'sid')
        self.assertNotIn('SECRET', str(record.call_args))

    def test_installer_preserves_hooks_and_is_idempotent(self):
        import shutil
        if not shutil.which('jq'):
            self.skipTest('jq unavailable')
        root = Path(q.__file__).resolve().parents[1]
        settings = self.state / 'settings.json'
        settings.write_text(json.dumps({'hooks': {'SessionStart': [{'hooks': [{'type': 'command', 'command': 'custom'}]}]}, 'keep': 42}))
        source = (root / 'install.sh').read_text()
        function = source[source.index('wire_claude_quota_hooks() {'):source.index('# ── hooks/ ──')]
        script = 'backup_settings_once() { :; }\nsettings_target() { echo "$SETTINGS"; }\n' + function + '\nwire_claude_quota_hooks\nwire_claude_quota_hooks\n'
        env = dict(os.environ, SRC_ROOT=str(root), CLAUDE_DIR=str(self.state), SETTINGS=str(settings))
        result = subprocess.run(['bash', '-c', script], env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(settings.read_text())
        self.assertEqual(data['keep'], 42)
        self.assertEqual(len(data['hooks']['SessionStart']), 2)
        self.assertEqual(len(data['hooks']['SessionEnd']), 1)


if __name__ == '__main__':
    unittest.main()
