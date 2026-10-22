import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import agent_profiles as p

class Profiles(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name);self.home=self.base/'home';self.home.mkdir()
        self.env=patch.dict(os.environ,{'HOME':str(self.home),'USERPROFILE':str(self.home),'PERSONAL_CODE_DIR':str(self.home/'personal code')})
        self.env.start();self.addCleanup(self.env.stop)
        (self.home/'personal code').mkdir()
    def repo(self,owner='jnelken'):
        r=self.base/('repo '+owner);r.mkdir();subprocess.run(['git','init','-q',str(r)],check=True)
        subprocess.run(['git','-C',str(r),'remote','add','origin',f'git@github.com:{owner}/example.git'],check=True)
        return r
    def test_code_root(self):
        root=self.home/'code';root.mkdir()
        self.assertEqual(p.classify(root,{})[0],'work')
        self.assertEqual(p.classify(self.home/'personal code',{})[0],'personal')
    def test_repository_ownership_beats_machine(self):
        r=self.repo();self.assertEqual(p.classify(r,{})[0],'personal')
        self.assertEqual(p.classify(self.repo('Concentro'),{})[0],'work')
    def test_unknown_remote_does_not_inherit_personal_grants(self):
        r=self.repo('someone');self.assertEqual(p.classify(r,{'roots':{str(self.base):'personal'}})[0],'unknown')
    def test_explicit_mapping_and_symlink(self):
        r=self.repo('someone');alias=self.base/'alias';alias.symlink_to(r)
        self.assertEqual(p.classify(p.path(alias),{'repositories':{str(r):'work'}})[0],'work')
    def test_unknown_and_invalid_mapping(self):
        self.assertEqual(p.resolve('development/auto',self.base,'codex',{})['profile'],'development/unknown')
        with self.assertRaises(ValueError):p.resolve('development/auto',self.base,'codex',{'roots':{str(self.base):'other'}})
    def test_provider_specific_fragments(self):
        r=self.repo()
        codex=p.resolve('development/auto',r,'codex',{})
        claude=p.resolve('development/auto',r,'claude',{})
        self.assertNotIn('Above-Sonnet',codex['instructions'])
        self.assertIn('Above-Sonnet',claude['instructions'])
        self.assertNotIn('Post-PR Workflow',codex['instructions'])
    def test_runtime_does_not_contain_dev_guidance(self):
        r=p.resolve('runtime/slackagent',self.base,'codex',{})
        self.assertNotIn('Superset Workspaces',r['instructions'])
        self.assertNotIn('push `main` freely',r['instructions'])
        self.assertEqual(len(r['sources']),3)
    def test_toml_array_of_inline_tables(self):
        # Python 3.11+ validates the exact syntax passed to Codex -c.
        import tomllib
        value=[{'path':'/a b','enabled':False}]
        self.assertEqual(tomllib.loads('x='+p.toml(value))['x'],value)
    def test_codex_isolation_and_prompt_integrity(self):
        r=p.resolve('runtime/text',self.base,'codex',{})
        args=p.runtime_args(r,'codex',['exec','-m','example','-c','model_reasoning_effort="high"','hello $(bad)'],{})
        self.assertIn('--ignore-user-config',args);self.assertIn('project_doc_max_bytes=0',args)
        self.assertIn('features.shell_tool=false',args)
        self.assertEqual(args[-2:],['--','hello $(bad)'])
        with self.assertRaises(ValueError):p.runtime_args(r,'codex',['exec','--dangerously-bypass-approvals-and-sandbox','hello'],{})
    def test_claude_isolation(self):
        r=p.resolve('runtime/text',self.base,'claude',{})
        args=p.runtime_args(r,'claude',['-p','hi','--tools','Bash','--setting-sources','project'],{})
        self.assertEqual(args[args.index('--tools')+1],'')
        self.assertIn('--safe-mode',args);self.assertNotIn('Bash',args)
        with self.assertRaises(ValueError):p.runtime_args(r,'claude',['--settings','bad'],{})
    def test_mcp_allowlist_and_text_calls(self):
        file=self.base/'mcp.json';file.write_text(json.dumps({'mcpServers':{'chrome-devtools':{'command':'chrome'}}}))
        with self.assertRaises(ValueError):p.runtime_mcp({},'claude',['--mcp-config',str(file)],False)
        self.assertEqual(p.runtime_mcp({},'claude',['--mcp-config',str(file)],True),({},[]))
        file.write_text(json.dumps({'mcpServers':{'mailcrush':{'command':'mailcrush'}}}))
        self.assertIn('mailcrush',p.runtime_mcp({},'claude',['--mcp-config',str(file)],False)[0])
    def test_installer_preserves_and_is_idempotent(self):
        spec=importlib.util.spec_from_file_location('installer',p.ROOT/'bin/install-agent-profiles')
        # extensionless entry point
        from importlib.machinery import SourceFileLoader
        loader=SourceFileLoader('installer',str(p.ROOT/'bin/install-agent-profiles'))
        spec=importlib.util.spec_from_loader('installer',loader);module=importlib.util.module_from_spec(spec);loader.exec_module(module)
        dest=self.home/'.claude/CLAUDE.md';dest.parent.mkdir();dest.write_text('local edits')
        module.install(p.ROOT,self.home)
        self.assertTrue(dest.is_symlink());self.assertEqual(dest.resolve(),p.ROOT/'instructions/bootstrap/CLAUDE.md')
        backups=list(dest.parent.glob('*.before-profiles-*'));self.assertEqual(backups[0].read_text(),'local edits')
        module.install(p.ROOT,self.home);self.assertEqual(len(list(dest.parent.glob('*.before-profiles-*'))),1)

    @unittest.skipIf(os.name == 'nt', 'Windows cannot exec a shebang script')
    def test_fake_cli_is_execed_and_exit_status_is_preserved(self):
        import sys
        fake=self.base/'custom-cli'
        fake.write_text('#!'+sys.executable+'\nimport sys,json,os\nif "--help" in sys.argv: print("--ignore-user-config --ignore-rules --strict-config");sys.exit(0)\nprint(json.dumps({"args":sys.argv[1:],"profile":os.environ["AGENT_PROFILE"]}))\nsys.exit(7)\n')
        fake.chmod(0o755)
        run=subprocess.run([sys.executable,str(p.ROOT/'bin/agent-profile'),'run','--profile','runtime/text','--harness','codex','--',str(fake),'exec','-m','example','literal $(bad)'],text=True,capture_output=True)
        self.assertEqual(run.returncode,7,run.stderr)
        data=json.loads(run.stdout)
        self.assertEqual(data['profile'],'runtime/text')
        self.assertIn('features.shell_tool=false',data['args'])
        self.assertEqual(data['args'][-1],'literal $(bad)')
    def test_worktree_uses_repository_owner(self):
        r=self.repo()
        subprocess.run(['git','-C',str(r),'-c','user.name=Test','-c','user.email=test@example.invalid','commit','--allow-empty','-qm','fixture'],check=True)
        wt=self.base/'worktree'
        subprocess.run(['git','-C',str(r),'worktree','add','-q','--detach',str(wt)],check=True)
        self.assertEqual(p.classify(wt,{})[0],'personal')

    def test_codex_mcp_translation_rejects_unknown_fields(self):
        self.assertEqual(p.codex_servers({'mailcrush':{'type':'http','url':'http://localhost:9999','headers':{'X-Example':'test'}}}),
                         {'mailcrush':{'url':'http://localhost:9999','http_headers':{'X-Example':'test'}}})
        with self.assertRaises(ValueError): p.codex_servers({'mailcrush':{'unknown':'bad'}})

    def test_bootstrap_glyph_contract_matches_provider_and_stop_hook(self):
        import re, shutil
        provider = (p.ROOT/'instructions/providers/claude-development.md').read_text(encoding='utf-8')
        glyphs = re.findall(r'^\| ([^\x00-\x7f]\S*) \|', provider, re.MULTILINE)
        self.assertTrue(glyphs, 'provider status table must define the accepted glyphs')
        bootstrap = (p.ROOT/'instructions/bootstrap/CLAUDE.md').read_text(encoding='utf-8')
        codex_bootstrap = (p.ROOT/'instructions/bootstrap/AGENTS.md').read_text(encoding='utf-8')
        for glyph in glyphs:
            self.assertIn(glyph, bootstrap)
            self.assertNotIn(glyph, codex_bootstrap)
            result = subprocess.run(
                [shutil.which('bash') or '/bin/bash', str(p.ROOT/'hooks/status-glyph-check.sh')],
                input=json.dumps({'last_assistant_message': 'Pong.\n\n'+glyph+' answer only',
                                  'stop_hook_active': False}),
                capture_output=True, encoding='utf-8', check=True,
            )
            self.assertEqual(result.stdout.strip(), '', 'bootstrap glyph rejected by Stop hook: '+glyph)

if __name__=='__main__':unittest.main()
