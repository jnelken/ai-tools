import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('audit', HERE / 'audit.py')
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


class AuditTest(unittest.TestCase):
    def test_groups_only_repeated_messages_within_one_channel_and_window(self):
        def m(ts, text): return {'ts': str(ts), 'text': text}
        data = {'eng': [m(1, 'repo skipped at 10:01'), m(100, 'repo skipped at 10:02'),
                        m(800, 'repo skipped at 10:03')],
                'ops': [m(1, 'other error'), m(1000, 'other error'), m(2000, 'other error')]}
        found = audit.groups(data)
        self.assertEqual([(x['channel'], x['count']) for x in found], [('eng', 3)])

    def test_clean_run_uses_no_model_or_repair(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(audit, 'ROOT', Path(tmp)), \
                patch.object(audit, 'run', return_value=SimpleNamespace(stdout=json.dumps({'eng': [], 'ops': []}))) as run:
            audit.main()
            self.assertEqual(run.call_count, 1)
            self.assertEqual(run.call_args.args[0][-1], str(HERE / 'fetch.mjs'))

    def test_unresolved_dispatch_prevents_second_worker(self):
        msgs = [{'ts': str(t), 'text':'repeated failure'} for t in (100,200,300)]
        with tempfile.TemporaryDirectory() as tmp, patch.object(audit, 'ROOT', Path(tmp)), \
                patch.object(audit, 'run', return_value=SimpleNamespace(stdout=json.dumps({'ops':msgs}))) as run:
            (Path(tmp)/'active-repair.json').write_text('{"status":"dispatching"}')
            audit.main()
            self.assertEqual(run.call_count, 1)

    def test_classifier_failure_never_dispatches_repair(self):
        msgs = [{'ts': str(t), 'text':'repeated failure'} for t in (100,200,300)]
        def execute(cmd, **kwargs):
            if cmd[-1].endswith('fetch.mjs'):
                return SimpleNamespace(stdout=json.dumps({'eng':msgs}))
            raise audit.subprocess.CalledProcessError(1, cmd)
        with tempfile.TemporaryDirectory() as tmp, patch.object(audit, 'ROOT', Path(tmp)), \
                patch.object(audit, 'run', side_effect=execute) as run:
            with self.assertRaises(audit.subprocess.CalledProcessError):
                audit.main()
            self.assertEqual(run.call_count, 2)
            self.assertFalse((Path(tmp)/'active-repair.json').exists())

    def test_confirmed_incident_dispatches_terra_once_in_new_workspace(self):
        msgs = [{'ts': str(t), 'text': 'repeated failure'} for t in (100, 200, 300)]
        signature = audit.groups({'eng': msgs})[0]['signature']
        def execute(cmd, **kwargs):
            if cmd[-1].endswith('fetch.mjs'):
                return SimpleNamespace(stdout=json.dumps({'eng': msgs}))
            if cmd[0] == audit.CODEX:
                Path(cmd[cmd.index('--output-last-message') + 1]).write_text(json.dumps(
                    {'action': 'investigate', 'reason': 'confirmed loop', 'signature': signature}))
                self.assertEqual(cmd[cmd.index('--model') + 1], 'gpt-5.6-luna')
                return SimpleNamespace(stdout='')
            self.assertEqual(cmd[cmd.index('--model') + 1], 'gpt-6.1-terra')
            return SimpleNamespace(stdout=json.dumps({'workspace': {'id': 'ws'},
                                                       'terminals': [{'terminalId': 'term'}]}))
        with tempfile.TemporaryDirectory() as tmp, patch.object(audit, 'ROOT', Path(tmp)), \
                patch.object(audit, 'run', side_effect=execute) as run:
            audit.main()
            self.assertEqual(run.call_count, 3)
            active = json.loads((Path(tmp)/'active-repair.json').read_text())
            self.assertEqual((active['workspace'], active['terminal']), ('ws', 'term'))

    def test_ignore_does_not_require_a_candidate_signature(self):
        msgs = [{'ts': str(t), 'text': 'repeated failure'} for t in (100, 200, 300)]
        def execute(cmd, **kwargs):
            if cmd[-1].endswith('fetch.mjs'):
                return SimpleNamespace(stdout=json.dumps({'ops': msgs}))
            Path(cmd[cmd.index('--output-last-message') + 1]).write_text(json.dumps(
                {'action': 'ignore', 'reason': 'already resolved', 'signature': ''}))
            return SimpleNamespace(stdout='')
        with tempfile.TemporaryDirectory() as tmp, patch.object(audit, 'ROOT', Path(tmp)), \
                patch.object(audit, 'run', side_effect=execute) as run:
            audit.main()
            self.assertEqual(run.call_count, 2)
            self.assertFalse((Path(tmp)/'active-repair.json').exists())


if __name__ == '__main__': unittest.main()
