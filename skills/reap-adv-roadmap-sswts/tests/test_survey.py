import importlib.util
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('survey', Path(__file__).parents[1] / 'scripts/survey.py')
survey = importlib.util.module_from_spec(spec)
spec.loader.exec_module(survey)


def result(code=0, out='', err=''):
    return subprocess.CompletedProcess([], code, out, err)


class HolderTests(unittest.TestCase):
    def holders(self, ps, kids, live_tabs=None, wt='/wt'):
        def fake(*args, **kw):
            if args[0] == 'lsof':
                return result(0, ''.join(f'p{p}\nfcwd\nn{wt}\n' for p in ps))
            if args[0] == 'ps':
                return result(0, ps[args[-1]])
            return result(0, kids.get(args[-1], ''))
        with patch.object(survey, 'run', side_effect=fake), patch.object(survey.os.path, 'realpath', side_effect=lambda x: x):
            return survey.holders(wt, live_tabs)

    def test_only_idle_login_shell_under_launchd_is_a_tab_shell(self):
        got = self.holders({'1': '1 /bin/zsh -l', '2': '1 /bin/zsh -l', '3': '5 -zsh', '4': '1 node server.js'},
                           {'2': '9\n'})
        self.assertEqual([h['idle_tab_shell'] for h in got], [True, False, False, False])

    def test_more_idle_shells_than_live_tabs_disqualifies_all(self):
        got = self.holders({'1': '1 /bin/zsh -l', '2': '1 /bin/zsh -l'}, {}, live_tabs=1)
        self.assertEqual([h['idle_tab_shell'] for h in got], [False, False])


class SafetyTests(unittest.TestCase):
    def test_json_buffer_banner_is_classified_from_text_field(self):
        import json
        banner = '=== worker used=cursor exit=0 finished Tue Nov 24 09:07:12 EST 2026 ==='
        raw = json.dumps({'terminalId': 't', 'text': 'work done\n' + banner + '\n\n~/wt ❯   \n'})
        self.assertEqual(survey.classify_tab(raw)['kind'], 'other')
        self.assertEqual(survey.classify_tab(survey.tab_text(raw))['kind'], 'finished0')
        self.assertEqual(survey.tab_text('plain buffer'), 'plain buffer')

    def test_empty_or_unknown_tab_is_not_setup(self):
        for raw in ('', 'active agent thinking'):
            with self.subTest(raw=raw):
                self.assertEqual(survey.classify_tab(raw)['kind'], 'other')

    def test_timezones_and_unknown_dates(self):
        self.assertLess(survey.parse_finished_date('Sun Nov 15 09:04:10 EDT 2026'),
                        survey.parse_finished_date('Sun Nov 15 09:04:10 EST 2026'))
        self.assertIsNone(survey.parse_finished_date('invalid'))

    def test_merge_failures_and_conflicting_net_new_fail_closed(self):
        cases = [
            ([result(2, 'tree\n')], 'error'),
            ([result(1, 'tree\nfile\n\n'), result(1, '', 'bad')], 'error'),
            ([result(1, 'tree\nfile\n\n'), result(0, ' other | 1 +\n 1 file changed')], 'net-new'),
            ([result(1, 'tree\nfile\n\n'), result()], 'conflicts'),
            ([result(0, 'tree\n'), result()], 'empty'),
        ]
        for responses, expected in cases:
            with self.subTest(expected=expected), patch.object(survey, 'run', side_effect=responses):
                self.assertEqual(survey.net_new_vs_main('/repo', 'branch', 'main')[0], expected)

    def test_done_requires_complete_pr_lookup(self):
        entry = {'root': '/repo', 'branch': 'branch', 'label_ticket': 'DEV-1'}
        with patch.object(survey, 'github_repo', return_value='owner/repo'):
            self.assertFalse(survey.finished_override(entry, {}, {'DEV-1': 'Done'})[0])
            self.assertTrue(survey.finished_override(entry, {('owner/repo', ''): []}, {'DEV-1': 'Done'})[0])
            self.assertFalse(survey.finished_override(entry, {('owner/repo', ''): [], ('owner/repo', 'branch'): [{'state': 'OPEN'}]}, {'DEV-1': 'Done'})[0])

    def test_failed_status_keeps_dead_pane(self):
        keep, reap = [], []
        with patch.object(survey.os.path, 'isdir', return_value=True), patch.object(survey, 'run', return_value=result(1)):
            survey._classify_dead_pane({}, {'branch': 'branch'}, '/wt', '/root', 'main', reap, keep)
        self.assertEqual(keep[0]['reason'], 'dead-pane-status-failed')
        self.assertFalse(reap)

    def test_lsof_failure_stops_survey(self):
        with patch.object(survey, 'run', return_value=result(1)):
            with self.assertRaises(RuntimeError):
                survey.held_paths()


if __name__ == '__main__':
    unittest.main()
