// Entry point 2. It delegates to the owner and never touches the raw client.
export function importRows(rows, { ledger }) {
  for (const row of rows) ledger.append({ role: 'importer', amountCents: row.amountCents });
}
