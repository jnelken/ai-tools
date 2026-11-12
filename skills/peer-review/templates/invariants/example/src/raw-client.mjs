// The raw side effect. Nothing but the owner (ledger.mjs) may import this file,
// and the public barrel (index.mjs) must not re-export it.
// Stand-in for a database, HTTP or payment client.
export function createRawClient(sink = []) {
  return { write: (record) => { sink.push(record); }, sink };
}
