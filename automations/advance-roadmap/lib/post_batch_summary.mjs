// Keep slackagent's formatter, credentials and retry/join behavior, adding the
// batch budget line before its single post. No changes to slackagent required.
import { createRequire } from 'node:module';
import { dirname, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { parseArgs } from 'node:util';

const { values } = parseArgs({ options: {
  script: { type: 'string' }, batch: { type: 'string' }, runs: { type: 'string' },
  'hold-line': { type: 'string' }, 'dry-run': { type: 'boolean', default: false },
} });
const script = resolve(values.script);
const require = createRequire(script);
require('dotenv').config({ path: resolve(dirname(script), '../.env'), quiet: true });
const summary = await import(pathToFileURL(resolve(dirname(script), '../src/roadmap-summary.js')).href);
const state = summary.loadBatchState(values.batch);
const records = summary.loadRunRecords(values.runs, (state.queue ?? []).map(entry => entry.stamp));
const appendHold = text => [text, values['hold-line']].filter(Boolean).join('\n');
if (values['dry-run']) {
  console.log(appendHold(summary.formatBatchSummary(state, records)));
} else {
  if (!process.env.SLACK_BOT_TOKEN) throw new Error('SLACK_BOT_TOKEN is not set');
  const { WebClient } = require('@slack/web-api');
  const client = new WebClient(process.env.SLACK_BOT_TOKEN);
  const post = client.chat.postMessage.bind(client.chat);
  client.chat.postMessage = args => post({ ...args, text: appendHold(args.text) });
  await summary.postBatchSummary({ client, state, records,
    channel: process.env.ADVANCE_ROADMAP_SLACK_CHANNEL || summary.DEFAULT_ROADMAP_CHANNEL });
}
