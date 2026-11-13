import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]


class RunNotificationTest(unittest.TestCase):
    def test_each_outcome_uses_the_recorded_stamp_and_slackagent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'scripts').mkdir()
            (root / 'src').mkdir()
            (root / 'node_modules/dotenv').mkdir(parents=True)
            (root / 'node_modules/@slack/web-api').mkdir(parents=True)
            (root / 'package.json').write_text('{"type":"module"}')
            script = root / 'scripts/post-roadmap-summary.mjs'
            script.touch()
            (root / 'node_modules/dotenv/index.js').write_text('exports.config = () => ({});')
            (root / 'node_modules/@slack/web-api/index.js').write_text('exports.WebClient = class {};')
            (root / 'src/roadmap-summary.js').write_text('''
import { readFileSync, appendFileSync } from 'node:fs';
export const DEFAULT_ROADMAP_CHANNEL = '#eng';
export const loadRunRecord = (path, stamp) => readFileSync(path, 'utf8').trim().split('\\n')
  .map(JSON.parse).find(row => row.stamp === stamp);
export const formatRoadmapSummary = row => row.outcome;
export const postRoadmapSummary = async ({record, channel}) =>
  appendFileSync(process.env.TEST_POSTS, JSON.stringify({stamp: record.stamp, outcome: record.outcome, channel}) + '\\n');
''')
            runs = root / 'runs.jsonl'
            runs.write_text(''.join(json.dumps({'stamp': stamp, 'outcome': outcome}) + '\n'
                                    for stamp, outcome in [('one', 'yielded'), ('two', 'skipped-quota')]))
            posts = root / 'posts'
            env = {**os.environ, 'SLACK_BOT_TOKEN': 'test', 'TEST_POSTS': str(posts)}
            for stamp in ('one', 'two'):
                subprocess.run(['node', str(HERE / 'lib/post_run_summary.mjs'), '--script', str(script),
                                '--stamp', stamp, '--runs', str(runs)], env=env, check=True)
            rows = [json.loads(line) for line in posts.read_text().splitlines()]
            self.assertEqual([(r['stamp'], r['outcome']) for r in rows],
                             [('one', 'yielded'), ('two', 'skipped-quota')])
            self.assertTrue(all(r['channel'] == '#eng' for r in rows))


if __name__ == '__main__':
    unittest.main()
