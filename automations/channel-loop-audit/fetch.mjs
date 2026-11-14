import { createRequire } from 'node:module';
import { resolve } from 'node:path';

const base = process.env.SLACKAGENT_DIR || `${process.env.HOME}/Dropbox/code/slackagent`;
const require = createRequire(resolve(base, 'package.json'));
require('dotenv').config({ path: resolve(base, '.env'), quiet: true });
const { WebClient } = require('@slack/web-api');
if (!process.env.SLACK_BOT_TOKEN) throw new Error('Slack token unavailable');
const client = new WebClient(process.env.SLACK_BOT_TOKEN);
const oldest = String(Math.floor(Date.now() / 1000) - 45 * 60);
const output = {};
for (const [name, channel] of [['eng','C0C598XFYJ0'], ['ops','C0C3YNC5ZC0']]) {
  if (process.argv[2] && process.argv[2] !== name) continue;
  let cursor;
  const messages = [];
  do {
    const result = await client.conversations.history({ channel, oldest, limit: 100, ...(cursor ? { cursor } : {}) });
    for (const m of result.messages || []) messages.push({ ts: m.ts, text: m.text || '', bot_id: m.bot_id || null, user: m.user || null });
    cursor = result.response_metadata?.next_cursor || undefined;
  } while (cursor && messages.length < 1000);
  if (cursor) throw new Error(`Slack history pagination limit reached for ${name}`);
  output[name] = messages;
}
console.log(JSON.stringify(output));
