// Use slackagent's formatter and credentials, including for quota-skipped runs.
import { createRequire } from 'node:module';
import { dirname, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { parseArgs } from 'node:util';
import { readFileSync } from 'node:fs';

const { values } = parseArgs({ options: {
  script: { type: 'string' }, stamp: { type: 'string' }, runs: { type: 'string' },
  'dry-run': { type: 'boolean', default: false },
} });
if (!values.script || !values.stamp || !values.runs) throw new Error('script, stamp and runs are required');
const script = resolve(values.script);
const require = createRequire(script);
require('dotenv').config({ path: resolve(dirname(script), '../.env'), quiet: true });
const summary = await import(pathToFileURL(resolve(dirname(script), '../src/roadmap-summary.js')).href);
const record = summary.loadRunRecord(values.runs, values.stamp);
if (!record) throw new Error(`No run record found for ${values.stamp}`);
// The stored outcome remains unchanged; label actionable yielded runs for people.
if (record.outcome === 'yielded') {
  let result = {};
  try { result = JSON.parse(readFileSync(resolve(dirname(values.runs), 'runs', values.stamp, 'worker-result.json'))); }
  catch { /* Older records may not have a worker result. */ }
  let review = {};
  try {
    const lines = readFileSync(resolve(dirname(values.runs), 'runs', values.stamp, 'reviews.jsonl'), 'utf8').trim().split('\n');
    review = JSON.parse(lines.at(-1));
  } catch { /* Review evidence is optional. */ }
  const reason = record.incremental?.lifecycle?.reason;
  if (result.needs_input) record.outcome = 'needs-input';
  else if (['quota-ceiling', 'quota-target'].includes(reason) ||
           (review.verdict === 'unavailable' && (review.gated?.length || review.limited?.length))) record.outcome = 'quota-paused';
  else if (['watchdog', 'guard-failed'].includes(reason)) record.outcome = 'error';
}
if (values['dry-run']) {
  console.log(summary.formatRoadmapSummary(record));
} else {
  if (!process.env.SLACK_BOT_TOKEN) throw new Error('SLACK_BOT_TOKEN is not set');
  const { WebClient } = require('@slack/web-api');
  const response = await summary.postRoadmapSummary({ client: new WebClient(process.env.SLACK_BOT_TOKEN), record,
    channel: process.env.ADVANCE_ROADMAP_SLACK_CHANNEL || summary.DEFAULT_ROADMAP_CHANNEL });
  console.log(`Posted run ${record.stamp} (${response?.ts || 'accepted'})`);
}
