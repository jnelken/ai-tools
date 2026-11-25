#!/usr/bin/env python3
"""Link the J3u OSX plugin into SwiftBar's plugin folder, preserving other plugins."""
import os
import subprocess
from pathlib import Path


def main():
    home = Path.home()
    source = Path(os.environ.get("AI_TOOLS_HOME", home / ".ai-tools")) / "apps/j3u-osx/j3u.15m.py"
    if not source.is_file():
        raise SystemExit("Deploy ai-tools first: ~/.ai-tools/install.sh")
    if not Path("/Applications/SwiftBar.app").is_dir():
        raise SystemExit("Install SwiftBar first: brew install --cask swiftbar")
    cur = subprocess.run(["/usr/bin/defaults", "read", "com.ameba.SwiftBar", "PluginDirectory"], text=True, capture_output=True)
    plugins = Path(cur.stdout.strip()).expanduser() if cur.returncode == 0 else home / "Library/Application Support/SwiftBar/Plugins"
    plugins.mkdir(parents=True, exist_ok=True)
    link = plugins / source.name
    if link.is_symlink() and link.resolve() == source.resolve():
        pass
    elif link.exists() or link.is_symlink():
        raise SystemExit("Existing plugin preserved; move it aside first: %s" % link)
    else:
        link.symlink_to(source)
    subprocess.run(["/usr/bin/defaults", "write", "com.ameba.SwiftBar", "PluginDirectory", "-string", str(plugins)], check=True)
    subprocess.run(["/usr/bin/open", "-g", "/Applications/SwiftBar.app"], check=True)
    subprocess.run(["/usr/bin/open", "-g", "swiftbar://refreshallplugins"], check=True)
    print("Installed:", link, "->", link.resolve())


if __name__ == "__main__":
    main()
