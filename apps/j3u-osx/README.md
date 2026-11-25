# J3u OSX

SwiftBar menu bar readout of unarchived Linear issues in the personal workspace (`jnelken`) against
the 250 cap. Turns orange at 80% and red at 90%. The menu has the state breakdown, **Refresh now**
(runs `linear-issue-count`'s `count.py` and records a new history point) and a Linear link.

```sh
brew install --cask swiftbar
~/.ai-tools/install.sh
python3 ~/.ai-tools/apps/j3u-osx/install.py
```

The plugin only reads `~/.claude/automations/linear-issue-count/state/history.jsonl`, so its 15-minute
refresh makes no API calls; the weekly automation and **Refresh now** write the data.
