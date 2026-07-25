// Sets the process title for any Node process launched with
// NODE_OPTIONS=--require pointing here.
//
// Priority: explicit PROCESS_NAME (set by the Claude Bash PreToolUse hook or
// per-MCP-server env), else the basename of the script being executed — which
// auto-names MCP servers (playwright-mcp, context7-mcp, ...) that spawn
// outside the Bash hook. The absolute-path-and-exists guard keeps subcommand
// argv like `claude daemon run` from being mistaken for a script name.
if (process.env.PROCESS_NAME) {
  process.title = process.env.PROCESS_NAME;
} else {
  try {
    const entry = process.argv[1];
    if (entry) {
      const path = require("path");
      const fs = require("fs");
      if (path.isAbsolute(entry) && fs.existsSync(entry)) {
        const base = path.basename(entry).replace(/\.(c|m)?js$/, "");
        if (base && base !== "node") process.title = base;
      }
    }
  } catch {
    // never break the host process over a cosmetic title
  }
}
