#!/usr/bin/env python3
"""Install the agent counter from the maintained deploy clone, preserving other plugins."""
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess


def main():
    home = Path.home()
    source = Path(os.environ.get("AI_TOOLS_HOME", home / ".ai-tools")) / "apps/agent-count"
    if not (source / "collector.py").is_file():
        raise SystemExit("Deploy ai-tools first: ~/.ai-tools/install.sh")
    app = Path("/Applications/SwiftBar.app")
    if not app.is_dir():
        raise SystemExit("Install SwiftBar first: brew install --cask swiftbar")
    pref = ["/usr/bin/defaults", "read", "com.ameba.SwiftBar", "PluginDirectory"]
    current = subprocess.run(pref, text=True, capture_output=True)
    plugins = Path(current.stdout.strip()).expanduser() if current.returncode == 0 else home / "Library/Application Support/SwiftBar/Plugins"
    plugins.mkdir(parents=True, exist_ok=True)
    link = plugins / "agents.5s.sh"
    target = source / link.name
    if link.is_symlink() and link.resolve() == target.resolve():
        pass
    elif link.exists() or link.is_symlink():
        raise SystemExit("Existing plugin preserved; move it aside before installing: %s" % link)
    else:
        link.symlink_to(target)
    if not os.access(link, os.X_OK):
        raise SystemExit("Plugin must be executable in the source checkout")
    subprocess.run(["/usr/bin/defaults", "write", "com.ameba.SwiftBar", "PluginDirectory", "-string", str(plugins)], check=True)
    # Capture the installed CLI path: GUI apps do not inherit shell aliases/PATH.
    config = home / ".config/agent-count/config.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    settings = json.loads(config.read_text()) if config.exists() else {}
    binary = shutil.which("superset")
    if binary:
        settings["superset"] = str(Path(binary).resolve())
    config.write_text(json.dumps(settings, indent=2) + "\n")
    agents = home / "Library/LaunchAgents"
    agents.mkdir(parents=True, exist_ok=True)
    plist = agents / "com.jnelken.agent-count.plist"
    content = {"Label": "com.jnelken.agent-count", "RunAtLoad": True,
               "ProgramArguments": ["/usr/bin/open", "-g", str(app)],
               "LimitLoadToSessionType": "Aqua"}
    plist.write_bytes(plistlib.dumps(content))
    domain = "gui/%s" % os.getuid()
    loaded = subprocess.run(["/bin/launchctl", "print", domain + "/com.jnelken.agent-count"], capture_output=True)
    if loaded.returncode != 0:
        subprocess.run(["/bin/launchctl", "bootstrap", domain, str(plist)], check=True)
    subprocess.run(["/usr/bin/open", "-g", str(app)], check=True)
    subprocess.run(["/usr/bin/open", "-g", "swiftbar://refreshallplugins"], check=True)
    print("Installed:", link, "->", link.resolve())
    print("Login startup:", plist)


if __name__ == "__main__":
    main()
