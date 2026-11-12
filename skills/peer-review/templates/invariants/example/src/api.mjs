// Entry point 1. It delegates to the owner and never touches the raw client.
export function createApi({ ledger }) {
  return {
    postPayment: (req) => ledger.append({ role: req.role, amountCents: req.amountCents }),
  };
}
