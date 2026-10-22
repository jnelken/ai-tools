"""Instruction recipes. No model calls, shell interpolation, credential copying, or network discovery."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from personal_code_dir import code_dir

ROOT = Path(__file__).resolve().parents[1]
PROFILES = ('development/auto', 'development/work', 'development/personal', 'development/unknown',
            'runtime/slackagent', 'runtime/text')

def path(value):
    return Path(os.path.expandvars(os.path.expanduser(str(value)))).resolve()

def within(target, parent):
    return target == parent or parent in target.parents

def git(target, *args):
    r = subprocess.run(['git', '-C', str(target), *args], capture_output=True, encoding='utf-8', errors='replace')
    return r.stdout.strip() if r.returncode == 0 else ''

def configuration():
    p = path(os.environ.get('AGENT_PROFILE_CONFIG', '~/.config/agent-profiles/config.json'))
    return (json.loads(p.read_text(encoding='utf-8')) if p.exists() else {}), p

def classify(target, config):
    target = path(target)
    for root, environment in sorted(config.get('repositories', {}).items(), key=lambda kv: -len(str(path(kv[0])))):
        if within(target, path(root)):
            return environment, 'explicit repository mapping'
    origin = git(target, 'remote', 'get-url', 'origin')
    if re.search(r'[:/]jnelken/[^/]+?(?:\.git)?/?$', origin):
        return 'personal', 'jnelken repository ownership'
    if origin:
        for owner in config.get('work_owners', ['Concentro', 'concentro']):
            if re.search(r'[:/]' + re.escape(owner) + r'/[^/]+?(?:\.git)?/?$', origin, re.I):
                return 'work', 'configured work repository ownership'
        # Do not grant personal permissions to an unidentified repo under a personal root.
        return 'unknown', 'repository owner is not classified; configure a repository mapping'
    roots = dict(config.get('roots', {}))
    for value in [code_dir(), os.environ.get('PERSONAL_CODE_DIR'), '~/Dropbox/code', '~/Library/CloudStorage/Dropbox/code']:
        if value and path(value).is_dir(): roots.setdefault(str(path(value)), 'personal')
    roots.setdefault(str(path('~/code')), 'work')
    for root, environment in sorted(roots.items(), key=lambda kv: -len(str(path(kv[0])))):
        if within(target, path(root)): return environment, 'configured/default code root'
    return 'unknown', 'outside configured development roots'

def resolve(profile, target, harness, config):
    if profile not in PROFILES: raise ValueError('unknown profile: ' + profile)
    reason = 'explicit profile'
    if profile == 'development/auto':
        environment, reason = classify(target, config)
        if environment not in ('personal', 'work', 'unknown'): raise ValueError('invalid environment mapping')
        profile = 'development/' + environment
    sources = ['shared.md']
    if profile.startswith('development/'):
        sources += ['development/switching.md', 'development/shared.md']
        if profile != 'development/unknown': sources += [profile + '.md']
        if harness == 'claude': sources += ['providers/claude-development.md']
    else:
        sources += ['runtime/shared.md', 'runtime/text.md' if profile == 'runtime/text' else 'runtime/slackagent.md']
    files = [ROOT / 'instructions' / source for source in sources]
    text = '\n\n'.join('# Source: ' + source + '\n\n' + f.read_text(encoding='utf-8') for source, f in zip(sources, files))
    text = text.replace('${AI_TOOLS_HOME}', str(ROOT))
    return {'profile': profile, 'target': str(target), 'reason': reason,
            'sources': [str(f) for f in files], 'instructions': text}

def toml(value):
    # JSON strings, booleans and arrays are also TOML expressions for our scalar overrides.
    if isinstance(value, list):
        return "[" + ", ".join(toml(v) for v in value) + "]"
    if isinstance(value, dict):
        return '{' + ', '.join(json.dumps(k) + '=' + toml(v) for k,v in value.items()) + '}'
    return json.dumps(value, ensure_ascii=False)

def overrides(values):
    result = []
    for k,v in values.items(): result += ['-c', k + '=' + toml(v)]
    return result

def runtime_mcp(config, harness, cli_args, text_only):
    """Only explicit local configurations; never import the user's entire connector catalog."""
    files = list(config.get('runtime', {}).get(harness, {}).get('mcp_files', []))
    remaining = []
    it = iter(cli_args)
    for arg in it:
        if arg == '--mcp-config': files.append(next(it))
        else: remaining.append(arg)
    servers = {}
    allowed = config.get('runtime', {}).get('allowed_mcp_names', ['mailcrush', 'linear', 'lunch-money', 'google-calendar', 'gmail'])
    if text_only: return {}, remaining
    for name in files:
        data = json.loads(path(name).read_text(encoding='utf-8')).get('mcpServers', {})
        for server, definition in data.items():
            if server not in allowed: raise ValueError('runtime MCP server is not allowlisted: ' + server)
            servers[server] = definition
    return servers, remaining

def disabled_skills(target):
    roots = [path('~/.agents/skills'), path('~/.codex/skills')]
    for parent in [target, *target.parents]: roots += [parent/'.agents/skills', parent/'.codex/skills']
    skills = []
    for root in roots:
        if root.is_dir():
            for manifest in root.glob('*/SKILL.md'):
                skills.append({'path': str(manifest.parent.resolve()), 'enabled': False})
            for manifest in root.glob('.system/*/SKILL.md'):
                skills.append({'path': str(manifest.parent.resolve()), 'enabled': False})
    return list({s['path']: s for s in skills}.values())

def codex_servers(servers):
    allowed = {'command', 'args', 'env', 'cwd', 'url', 'http_headers', 'env_http_headers',
               'bearer_token_env_var', 'startup_timeout_sec', 'tool_timeout_sec',
               'enabled_tools', 'disabled_tools'}
    converted = {}
    for name, definition in servers.items():
        item = dict(definition)
        item.pop('type', None)
        if 'headers' in item:
            item['http_headers'] = item.pop('headers')
        unknown = set(item) - allowed
        if unknown:
            raise ValueError('unsupported Codex MCP fields for ' + name + ': ' + ', '.join(sorted(unknown)))
        converted[name] = item
    return converted

def runtime_args(result, harness, args, config):
    text_only = result['profile'] == 'runtime/text'
    servers, args = runtime_mcp(config, harness, args, text_only)
    if harness == 'claude':
        # Safe mode keeps subscription auth; bare mode does not. Explicit MCP is verified by smoke tests.
        for flag in ('--append-system-prompt', '--system-prompt', '--settings', '--plugin-dir', '--agents', '--agent', '--add-dir', '--dangerously-skip-permissions', '--allow-dangerously-skip-permissions', '--permission-mode', '--allowedTools', '--allowed-tools'):
            if any(a == flag or a.startswith(flag+'=') for a in args):
                raise ValueError('runtime launch cannot override ' + flag)
        for arg in args:
            if arg.startswith(('--tools=', '--setting-sources=', '--mcp-config=')):
                raise ValueError('use separate arguments for runtime controls: ' + arg.split('=')[0])
        # Normalize app-supplied lightweight flags; the profile owns these controls.
        for flag in ('--tools', '--setting-sources'):
            while flag in args:
                i=args.index(flag); del args[i:i+2]
        args = [a for a in args if a not in ('--safe-mode', '--strict-mcp-config')]
        mode = ['--safe-mode'] if not servers else ['--restricted', '--disable-slash-commands', '--settings', json.dumps({'disableAllHooks': True, 'autoMemoryEnabled': False})]
        return args + mode + ['--setting-sources', '', '--strict-mcp-config',
                       '--mcp-config', json.dumps({'mcpServers': servers}), '--tools', '',
                       '--append-system-prompt', result['instructions']]
    if not args or args[0] != 'exec': raise ValueError('runtime Codex requires codex exec')
    # Only known application arguments can precede the prompt; reject configuration escape hatches.
    valued = {'-m', '--model', '-o', '--output-last-message', '--color', '--output-schema'}
    switches = {'--ephemeral', '--skip-git-repo-check', '--json'}
    clean = []; prompt = []; i = 1
    while i < len(args):
        arg=args[i]
        if arg in valued:
            clean += args[i:i+2]; i += 2
        elif arg in switches:
            clean.append(arg); i += 1
        elif arg in ('-c','--config') and i+1<len(args) and re.fullmatch(r'model_reasoning_effort="(?:none|minimal|low|medium|high|xhigh|max|ultra)"',args[i+1]):
            clean += args[i:i+2]; i += 2
        elif arg == '--':
            prompt=args[i+1:];break
        elif not arg.startswith('-'):
            prompt=args[i:];break
        else: raise ValueError('unsupported runtime Codex option: ' + arg)
    if len(prompt)>1: raise ValueError('runtime Codex accepts one prompt argument')
    values = {'project_doc_max_bytes': 0, 'developer_instructions': result['instructions'],
              'features.hooks': False, 'features.memories': False, 'features.multi_agent': False,
              'features.apps': False, 'features.shell_tool': False, 'features.shell_snapshot': False,
              'features.skill_mcp_dependency_install': False,
              'tools.web_search': False,
              'features.browser_use': False, 'features.browser_use_external': False,
              'features.computer_use': False, 'features.image_generation': False,
              'skills.config': disabled_skills(path(result['target'])),
              'sandbox_mode': 'read-only', 'approval_policy': 'never',
              'mcp_servers': codex_servers(servers)}
    # Project configuration must not reintroduce integrations/hooks. CLI trust wins over local trust.
    root = git(path(result['target']), 'rev-parse', '--show-toplevel') or result['target']
    values['projects.' + json.dumps(root) + '.trust_level'] = 'untrusted'
    return ['exec', '--ignore-user-config', '--ignore-rules', '--strict-config', *clean,
            *overrides(values), *(['--', *prompt] if prompt else [])]

def main():
    # Windows consoles default to a legacy code page that can't print the status glyphs.
    sys.stdout.reconfigure(encoding='utf-8'); sys.stderr.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['resolve','run'])
    parser.add_argument('--profile', default=os.environ.get('AGENT_PROFILE','development/auto'))
    parser.add_argument('--target', default=os.getcwd())
    parser.add_argument('--harness', choices=['claude','codex'])
    parser.add_argument('--text', action='store_true')
    parser.add_argument('--explain', action='store_true')
    argv=sys.argv[1:]
    split=argv.index('--') if '--' in argv else len(argv)
    args=parser.parse_args(argv[:split]); command=argv[split+1:]
    try:
        config, config_path=configuration()
        harness=args.harness or 'codex'
        if command and not args.harness:
            harness='codex' if 'codex' in Path(command[0]).name.lower() else 'claude'
        target=path(args.target)
        result=resolve(args.profile,target,harness,config)
        if args.command=='resolve':
            if args.text: print(result['instructions'])
            else:
                del result['instructions'];result['config_file']=str(config_path)
                result['optional_runtime_mcp_files']=config.get('runtime',{}).get(harness,{}).get('mcp_files',[])
                print(json.dumps(result,indent=2))
            return
        if not command: raise ValueError('run requires -- CLI [arguments]')
        cli_args=command[1:]
        if result['profile'].startswith('runtime/'):
            # Reject unknown flags in the underlying CLI instead of silently losing isolation.
            help_args=['exec','--help'] if harness=='codex' else ['--help']
            help_text=subprocess.run([command[0],*help_args],capture_output=True,encoding='utf-8',errors='replace',check=True).stdout
            needed=['--ignore-user-config','--ignore-rules','--strict-config'] if harness=='codex' else ['--safe-mode','--restricted','--disable-slash-commands','--strict-mcp-config','--append-system-prompt']
            if any(flag not in help_text for flag in needed): raise ValueError('CLI version lacks required runtime isolation flags')
            if harness == 'claude':
                servers, _ = runtime_mcp(config, harness, cli_args, result['profile'] == 'runtime/text')
                if servers:
                    version = subprocess.run([command[0], '--version'], capture_output=True, encoding='utf-8', errors='replace', check=True).stdout.split()[0]
                    verified = config.get('runtime', {}).get('claude', {}).get('verified_connector_version', '2.1.303')
                    if version != verified:
                        raise ValueError('Claude connector isolation needs a smoke check for version ' + version + '; tested version: ' + verified)
            cli_args=runtime_args(result,harness,cli_args,config)
        elif harness=='claude':
            cli_args += ['--append-system-prompt',result['instructions']]
        else:
            cli_args += overrides({'developer_instructions':result['instructions']})
        env=dict(os.environ,AGENT_PROFILE=args.profile)
        if result['profile'].startswith('runtime/') and harness == 'claude':
            env.pop('CLAUDE_CODE_SAFE_MODE', None)
            env.update(CLAUDE_CODE_DISABLE_CLAUDE_MDS='1', CLAUDE_CODE_DISABLE_AUTO_MEMORY='1')
        os.execvpe(command[0],[command[0],*cli_args],env)
    except (ValueError,OSError,KeyError,StopIteration,subprocess.CalledProcessError) as e:
        print('agent-profile: '+str(e),file=sys.stderr);sys.exit(2)

if __name__=='__main__': main()
