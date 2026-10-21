"""Exercise the real installer against local Git remotes; no network or real home writes."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.author = self.base / 'author'
        self.deploy = self.base / 'deploy clone'
        self.remote = self.base / 'remote.git'
        self.test_home = self.base / 'test home'
        self.test_home.mkdir()
        self.env = dict(os.environ, HOME=str(self.test_home), AI_TOOLS_HOME=str(self.deploy),
                        AI_TOOLS_REPO=str(self.remote), AI_TOOLS_REF='main',
                        FIXTURE_MARKER=str(self.base / 'marker'),
                        GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull)
        self.env.pop('AI_TOOLS_INSTALL_RESTARTED', None)
        self.run_git('init', '-q', '--bare', str(self.remote))
        self.run_git('init', '-q', '-b', 'main', str(self.author))
        self.run_git('-C', str(self.author), 'config', 'user.name', 'Fixture')
        self.run_git('-C', str(self.author), 'config', 'user.email', 'fixture@example.invalid')
        self.run_git('-C', str(self.author), 'remote', 'add', 'origin', str(self.remote))
        (self.author / 'bin').mkdir()
        # The actual clone-lock helper is part of the integration, including read-only relocking.
        shutil.copy2(ROOT / 'bin/ai-tools-clone-lock', self.author / 'bin/ai-tools-clone-lock')
        (self.author / 'bin/install-agent-profiles').write_text('pass\n')
        (self.author / 'tracked file.py').write_text('original\n')
        (self.author / '.gitignore').write_text('__pycache__/\n')
        self.write_installer('old')
        self.commit('old fixture')
        self.run_git('-C', str(self.author), 'push', '-qu', 'origin', 'main')
        self.run_git('clone', '-q', '--branch', 'main', str(self.remote), str(self.deploy))
        self.addCleanup(self.unlock)
        self.bin_dir = self.base / 'test-bin'
        self.bin_dir.mkdir()
        # Minimal fixture has no settings consumers, so jq only needs to exist for preflight.
        (self.bin_dir / 'jq').write_text('#!/bin/sh\nexit 0\n')
        (self.bin_dir / 'jq').chmod(0o755)
        self.env['PATH'] = str(self.bin_dir) + os.pathsep + self.env['PATH']

    def run_git(self, *args):
        return subprocess.run(['git', *args], env=self.env, text=True, capture_output=True, check=True).stdout.strip()

    def write_installer(self, revision):
        source = (ROOT / 'install.sh').read_text()
        # A function parsed BEFORE the update proves we restarted: merely reading a newly
        # appended tail after merge cannot change the old cached function body.
        marker = '''fixture_revision() {
  printf '%s|%s|%s|%s\\n' 'REVISION' "$QUIET" "$NO_UPDATE" "${AI_TOOLS_INSTALL_RESTARTED:-0}" >> "$FIXTURE_MARKER"
}
'''.replace('REVISION', revision)
        source = source.replace('update_ai_tools_home() {', marker + 'update_ai_tools_home() {', 1)
        source += '\nfixture_revision\n'
        (self.author / 'install.sh').write_text(source)

    def commit(self, message):
        self.run_git('-C', str(self.author), 'add', '.')
        self.run_git('-C', str(self.author), 'commit', '-qm', message)

    def update_remote(self):
        self.write_installer('new')
        self.commit('new fixture')
        self.run_git('-C', str(self.author), 'push', '-q', 'origin', 'main')

    def install(self, *args, script=None, piped=False, env=None):
        script = script or self.deploy / 'install.sh'
        command = ['/bin/bash', '-s', '--', *args] if piped else ['/bin/bash', str(script), *args]
        return subprocess.run(command, input=Path(script).read_text() if piped else None,
                              env=env or self.env, text=True, capture_output=True, timeout=30)

    def marker(self):
        return (self.base / 'marker').read_text().splitlines()

    def unlock(self):
        if self.deploy.exists():
            subprocess.run(['/bin/bash', str(self.deploy/'bin/ai-tools-clone-lock'), 'unlock', '-q'],
                           env=self.env, capture_output=True)

    def test_update_restarts_once_and_preserves_quiet(self):
        self.update_remote()
        result = self.install('--quiet')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.marker(), ['new|1|1|1'])
        self.assertNotIn('Linking ~/.claude', result.stdout)
        self.assertEqual(self.run_git('-C', str(self.deploy), 'status', '--porcelain'), '')
        self.assertEqual(self.run_git('-C', str(self.deploy), 'rev-parse', 'HEAD'),
                         self.run_git('-C', str(self.author), 'rev-parse', 'HEAD'))

    def test_piped_update_uses_new_installer(self):
        self.update_remote()
        result = self.install('--quiet', piped=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.marker(), ['new|1|1|1'])

    def test_fresh_clone_restarts_into_deployed_installer(self):
        shutil.rmtree(self.deploy)
        result = self.install('--quiet', script=self.author/'install.sh')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.marker(), ['old|1|1|1'])

    def test_no_update_and_dev_do_not_restart(self):
        self.update_remote()
        result = self.install('--no-update')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.marker(), ['old|0|1|0'])
        result = self.install('--dev', '--quiet', script=self.author/'install.sh')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.marker()[-1], 'new|1|1|0')

    def test_unchanged_revision_does_not_restart(self):
        result = self.install('--quiet')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.marker(), ['old|1|0|0'])

    def test_restart_marker_prevents_another_restart(self):
        self.update_remote()
        result = self.install('--quiet', piped=True, env=dict(self.env, AI_TOOLS_INSTALL_RESTARTED='1'))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn('Continuing with the updated installer.', result.stdout)
        self.assertEqual(len(self.marker()), 1)

    def test_mode_only_drift_diagnostic_preserves_changes(self):
        self.update_remote()
        target = self.deploy/'tracked file.py'
        target.chmod(0o755)
        before = self.run_git('-C', str(self.deploy), 'diff', '--binary', 'HEAD')
        result = self.install('--quiet')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('mode-only deploy drift', result.stdout)
        self.assertIn('diff --binary HEAD', result.stdout)
        self.assertIn('clone-lock', result.stdout)
        self.assertIn('chmod a-x', result.stdout)
        self.assertIn('tracked\\ file.py', result.stdout)
        self.assertEqual(before, self.run_git('-C', str(self.deploy), 'diff', '--binary', 'HEAD'))
        self.assertEqual(self.marker(), ['old|1|0|0'])

    def test_staged_mode_and_content_are_reported_as_mixed(self):
        target = self.deploy/'tracked file.py'
        target.chmod(0o755)
        target.write_text('changed content\n')
        self.run_git('-C', str(self.deploy), 'add', 'tracked file.py')
        before = self.run_git('-C', str(self.deploy), 'diff', '--cached', '--binary')
        result = self.install('--quiet')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('alongside other edits', result.stdout)
        self.assertNotIn('Detected mode-only', result.stdout)
        self.assertEqual(before, self.run_git('-C', str(self.deploy), 'diff', '--cached', '--binary'))
        self.assertEqual(target.read_text(), 'changed content\n')

    def test_content_only_drift_keeps_generic_warning(self):
        (self.deploy/'tracked file.py').write_text('changed content\n')
        result = self.install('--quiet')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('uncommitted edits', result.stdout)
        self.assertNotIn('executable bits', result.stdout)
        self.assertEqual((self.deploy/'tracked file.py').read_text(), 'changed content\n')

    def test_update_with_no_arguments_is_bash32_safe(self):
        self.update_remote()
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.marker(), ['new|0|1|1'])

    def test_untracked_files_prevent_mode_only_label(self):
        (self.deploy/'tracked file.py').chmod(0o755)
        extra = self.deploy/'untracked.txt'
        extra.write_text('keep me\n')
        result = self.install('--quiet')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('alongside other edits', result.stdout)
        self.assertNotIn('Detected mode-only', result.stdout)
        self.assertEqual(extra.read_text(), 'keep me\n')

    def test_empty_tracked_addition_is_not_mode_only(self):
        (self.deploy/'tracked file.py').chmod(0o755)
        (self.deploy/'empty.txt').touch()
        self.run_git('-C', str(self.deploy), 'add', 'empty.txt')
        result = self.install('--quiet')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('alongside other edits', result.stdout)
        self.assertNotIn('Detected mode-only', result.stdout)
        self.assertTrue((self.deploy/'empty.txt').exists())

if __name__ == '__main__':
    unittest.main()
