// Keep slackagent's formatter, credentials and retry/join behavior in one post.
import { createRequire } from 'node:module';
import { dirname, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { parseArgs } from 'node:util';
import { existsSync } from 'node:fs';

const { values } = parseArgs({ options: {
  script: { type: 'string' }, batch: { type: 'string' }, runs: { type: 'string' },
  'dry-run': { type: 'boolean', default: false },
} });
const script = resolve(values.script);
const require = createRequire(script);
require('dotenv').config({ path: resolve(dirname(script), '../.env'), quiet: true });
const summary = await import(pathToFileURL(resolve(dirname(script), '../src/roadmap-summary.js')).href);
const state = summary.loadBatchState(values.batch);
const records = summary.loadRunRecords(values.runs, (state.queue ?? []).map(entry => entry.stamp));
const decisionsPath = resolve(dirname(script), '../src/roadmap-decisions.js');
const decisionsModule = existsSync(decisionsPath) ? await import(pathToFileURL(decisionsPath).href) : null;
if (decisionsModule) for (const record of records.values()) record.decision = await decisionsModule.loadRoadmapDecision(record, dirname(values.runs));
if (values['dry-run']) {
  console.log(summary.formatBatchSummary(state, records));
} else {
  if (!process.env.SLACK_BOT_TOKEN) throw new Error('SLACK_BOT_TOKEN is not set');
  const { WebClient } = require('@slack/web-api');
  const client = new WebClient(process.env.SLACK_BOT_TOKEN);
  const decisions = decisionsModule && new decisionsModule.RoadmapDecisions({ ownerUserId: process.env.SLACK_OWNER_USER_ID?.trim(), client, root: dirname(values.runs) });
  await summary.postBatchSummary({ client, state, records,
    channel: process.env.ADVANCE_ROADMAP_SLACK_CHANNEL || summary.DEFAULT_ROADMAP_CHANNEL,
    onPosted: decisions ? (message) => decisions.register(message) : undefined });
}
