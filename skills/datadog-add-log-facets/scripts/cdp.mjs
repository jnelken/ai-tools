// One CDP call over a Superset browser pane's WebSocket.
//   node cdp.mjs "$(superset browser cdp --workspace $W --pane $P)" <Domain.method> '<json params>'
// The URL comes from `superset browser cdp` (its JSON `url` field). Needs Node 22+ (global WebSocket).
// Real input is what React comboboxes need: Input.insertText for typing, Input.dispatchMouseEvent
// (mouseMoved/mousePressed/mouseReleased) for clicks, Input.dispatchKeyEvent for keys.
const [url, method, params = "{}"] = process.argv.slice(2);
const ws = new WebSocket(url);
ws.onopen = () => ws.send(JSON.stringify({ id: 1, method, params: JSON.parse(params) }));
ws.onmessage = (e) => {
  const m = JSON.parse(e.data);
  if (m.id === 1) {
    console.log(JSON.stringify(m.result ?? m.error));
    process.exit(0);
  }
};
ws.onerror = (e) => {
  console.error("ws error", e.message);
  process.exit(1);
};
setTimeout(() => {
  console.error("timeout");
  process.exit(2);
}, 15000);
