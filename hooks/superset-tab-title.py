#!/usr/bin/env python3
"""Keep the Superset terminal tab named after the work in it.

Wired as a UserPromptSubmit and Stop hook for Claude Code and Codex. On the
first prompt of a session it derives a kebab slug from the prompt and names
the tab "◐ <slug>"; every later prompt flips the glyph back to ◐ (working);
every Stop swaps in the sign-off glyph from the final assistant line
(🛑 ⚠️ ⏳ 💡 ✅ ⛏️ 💬, see the global CLAUDE.md). A name Jake typed in the
app's rename dialog is kept as the base; only the glyph changes.

The write goes to the Superset host service's loopback tRPC `terminal.rename`
(unsupported internal API, Superset ≥1.35; see memory note
superset-terminal-rename-local-trpc). Endpoint + bearer token come from
~/.superset/host/<orgId>/manifest.json, read at call time because the env's
SUPERSET_HOST_AGENT_HOOK_URL goes stale when the host restarts.

Always exits 0 and prints nothing on success: UserPromptSubmit stdout is
injected into the agent's context and Stop stdout is parsed as a decision.

    superset-tab-title.py --event prompt|stop [--harness claude|codex]

Env:
  SUPERSET_TAB_TITLE_DRY_RUN=1   print the title it would set, skip the write
  SUPERSET_TAB_TITLE_DEBUG=path  append raw stdin + decisions to that file
  SUPERSET_TAB_TITLE_STATE_DIR   override ~/.cache/superset-tab-title
"""

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

WORKING = "◐"  # ◐
IDLE = "○"  # ○ — a stop with no sign-off glyph (Codex, or a tool-call turn)
FE0F = "️"
SIGNOFF = ["\U0001f6d1", "⚠", "⏳", "\U0001f4a1", "✅", "⛏", "\U0001f4ac"]
GLYPH_CHARS = set(SIGNOFF + [WORKING, "◑", IDLE, FE0F])
BLOCKING_TOOLS = {"AskUserQuestion", "ExitPlanMode"}
STOPWORDS = set(
    """a an and another are at be been being can could did do does doing for
    from get got here how i implement implemented implementing in into is it
    its just let lets make making may me might must my of on or our please
    push shall should that the then there these this those to us use using
    was we were what when where why will with within would you your again
    now s re ve ll d t m""".split()
)
MAX_SLUG_WORDS = 5
MAX_SLUG_LEN = 32
TIMEOUT = 2.0


def debug(msg):
    path = os.environ.get("SUPERSET_TAB_TITLE_DEBUG")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(msg.rstrip("\n") + "\n")
    except OSError:
        pass


# ── input ────────────────────────────────────────────────────────────────────


def read_stdin():
    try:
        raw = sys.stdin.read()
    except Exception:
        return {}, ""
    try:
        data = json.loads(raw) if raw.strip() else {}
    except ValueError:
        data = {}
    return (data if isinstance(data, dict) else {}), raw


def extract_prompt(data):
    """Tolerant of both harnesses: Claude sends `prompt`; Codex's shape is
    unverified so several plausible keys are tried."""
    for key in ("prompt", "user_prompt", "text", "message", "input"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value
    items = data.get("items")
    if not isinstance(items, list):
        turn = data.get("payload", {})
        turn = turn.get("UserTurn", {}) if isinstance(turn, dict) else {}
        items = turn.get("items") if isinstance(turn, dict) else None
    if isinstance(items, list):
        texts = [i.get("text") for i in items if isinstance(i, dict) and isinstance(i.get("text"), str)]
        if texts:
            return " ".join(texts)
    return ""


def last_assistant_entry(transcript_path):
    """Last non-sidechain assistant entry of a JSONL transcript, or None."""
    if not transcript_path or not os.path.isfile(transcript_path):
        return None
    last = None
    try:
        with open(transcript_path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(entry, dict) or entry.get("type") != "assistant":
                    continue
                if entry.get("isSidechain"):
                    continue
                last = entry
    except OSError:
        return None
    return last


def final_text_and_tool(data):
    """(final assistant text, name of a trailing tool_use) for a Stop event."""
    text = data.get("last_assistant_message")
    if isinstance(text, str) and text.strip():
        return text, None
    entry = last_assistant_entry(data.get("transcript_path"))
    if entry is None:
        return "", None
    content = entry.get("message", {})
    content = content.get("content") if isinstance(content, dict) else None
    if isinstance(content, str):
        return content, None
    if not isinstance(content, list):
        return "", None
    texts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
    tools = [b.get("name", "") for b in content if isinstance(b, dict) and b.get("type") == "tool_use"]
    return "\n".join(t for t in texts if isinstance(t, str)), (tools[-1] if tools else None)


# ── title pieces ─────────────────────────────────────────────────────────────


def signoff_glyphs(text):
    """Leading glyph run of the last non-blank line, e.g. '⏳💡', or ''."""
    lines = [ln.rstrip() for ln in text.split("\n")]
    lines = [ln for ln in lines if ln.strip()]
    if not lines:
        return ""
    line = re.sub(r"^[—–-]\s*", "", lines[-1].strip())
    run = ""
    for ch in line:
        if ch in GLYPH_CHARS and ch not in (WORKING, "◑", IDLE):
            run += ch
        else:
            break
    # Must start with a real sign-off glyph, not a stray FE0F.
    return run if run and run[0] in SIGNOFF else ""


def split_glyph(title):
    """'⏳ foo' -> ('⏳', 'foo'); 'foo' -> ('', 'foo')."""
    i = 0
    while i < len(title) and title[i] in GLYPH_CHARS:
        i += 1
    return title[:i], title[i:].strip()


def slugify(text):
    text = text.lower()
    text = re.sub(r"[^\x00-\x7f]+", " ", text)
    text = re.sub(r"[`\"']", "", text)
    text = re.sub(r"[^a-z0-9-]+", " ", text)
    words = []
    for word in text.split():
        word = re.sub(r"-{2,}", "-", word.strip("-"))
        if not word or word in STOPWORDS:
            continue
        words.append(word)
        if len(words) == MAX_SLUG_WORDS:
            break
    if not words:
        return ""
    slug = "-".join(words)
    while len(slug) > MAX_SLUG_LEN and len(words) > 2:
        words.pop()
        slug = "-".join(words)
    if len(slug) > MAX_SLUG_LEN:
        slug = slug[:MAX_SLUG_LEN].rstrip("-")
    return slug


def fallback_slug(cwd):
    branch = ""
    try:
        import subprocess

        branch = subprocess.run(
            ["git", "-C", cwd or os.getcwd(), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=TIMEOUT,
        ).stdout.strip()
    except Exception:
        branch = ""
    if not branch or branch == "HEAD":
        branch = os.path.basename((cwd or os.getcwd()).rstrip("/"))
    return slugify(branch)


# ── host service ─────────────────────────────────────────────────────────────


def manifests():
    """[(endpoint, token)] — the env's org first, then every other org."""
    home = os.environ.get("SUPERSET_HOME_DIR") or os.path.expanduser("~/.superset")
    host_dir = os.path.join(home, "host")
    org = os.environ.get("SUPERSET_ORGANIZATION_ID", "")
    out = []
    try:
        names = sorted(os.listdir(host_dir), key=lambda n: (n != org, n))
    except OSError:
        return out
    for name in names:
        path = os.path.join(host_dir, name, "manifest.json")
        try:
            with open(path, encoding="utf-8") as fh:
                m = json.load(fh)
            if isinstance(m, dict) and m.get("endpoint") and m.get("authToken"):
                out.append((m["endpoint"].rstrip("/"), m["authToken"]))
        except (OSError, ValueError):
            continue
    return out


def trpc(endpoint, token, proc, payload, mutation):
    url = endpoint + "/trpc/" + proc
    body = json.dumps({"json": payload}).encode()
    headers = {"Authorization": "Bearer " + token, "Content-Type": "application/json"}
    if mutation:
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    else:
        req = urllib.request.Request(url + "?input=" + urllib.parse.quote(body.decode()), headers=headers)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode())


def current_custom_title(terminal_id, workspace_id):
    for endpoint, token in manifests():
        try:
            data = trpc(endpoint, token, "terminal.list", {"workspaceId": workspace_id}, False)
        except Exception as exc:  # noqa: BLE001 — any failure means "unknown"
            debug("list failed %s: %s" % (endpoint, exc))
            continue
        sessions = data.get("result", {}).get("data", {}).get("json", {}).get("sessions", [])
        for s in sessions:
            if isinstance(s, dict) and s.get("terminalId") == terminal_id:
                return s.get("customTitle") or "", (endpoint, token)
    return None, None


def rename(terminal_id, workspace_id, title, preferred=None):
    candidates = ([preferred] if preferred else []) + [m for m in manifests() if m != preferred]
    for endpoint, token in candidates:
        try:
            trpc(endpoint, token, "terminal.rename",
                 {"terminalId": terminal_id, "workspaceId": workspace_id, "title": title}, True)
            debug("renamed via %s -> %r" % (endpoint, title))
            return True
        except Exception as exc:  # noqa: BLE001
            debug("rename failed %s: %s" % (endpoint, exc))
    return False


# ── state ────────────────────────────────────────────────────────────────────


def state_path(terminal_id):
    d = os.environ.get("SUPERSET_TAB_TITLE_STATE_DIR") or os.path.expanduser("~/.cache/superset-tab-title")
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        return None
    return os.path.join(d, re.sub(r"[^A-Za-z0-9_-]", "_", terminal_id) + ".json")


def load_state(path):
    try:
        with open(path, encoding="utf-8") as fh:
            s = json.load(fh)
        return s if isinstance(s, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def save_state(path, state):
    if not path:
        return
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(state, fh)
    except OSError:
        pass


# ── main ─────────────────────────────────────────────────────────────────────


def decide(event, harness, data, existing, state):
    """Return (new_title or None, new_state). Pure, so tests can cover it."""
    session_id = str(data.get("session_id") or data.get("resourceId") or data.get("resource_id") or "")
    glyph, base = split_glyph(existing or "")
    ours = state.get("slug", "")
    new_state = dict(state)

    if event == "prompt":
        same_session = session_id and state.get("session_id") == session_id
        manual = base and base != ours  # a name we didn't set: keep it
        if manual or (base and same_session):
            slug = base
        else:
            slug = slugify(extract_prompt(data)) or fallback_slug(data.get("cwd", "")) or base
            if not slug:
                return None, new_state
            new_state["slug"] = slug
        if session_id:
            new_state["session_id"] = session_id
        return WORKING + " " + slug, new_state

    # stop
    if not base:
        return None, new_state  # nothing was ever named; a stop alone names nothing
    text, tool = final_text_and_tool(data)
    if not text.strip() and not tool:
        return None, new_state  # no information about how the turn ended
    run = signoff_glyphs(text)
    if not run:
        if tool in BLOCKING_TOOLS:
            run = SIGNOFF[0]  # 🛑 waiting on Jake
        elif harness == "claude" and text.strip():
            return None, new_state  # status-glyph-check will block; the glyph arrives on the next Stop
        else:
            run = IDLE
    return run + " " + base, new_state


def main(argv):
    event = "prompt"
    harness = "claude"
    args = list(argv)
    while args:
        a = args.pop(0)
        if a == "--event" and args:
            event = args.pop(0)
        elif a == "--harness" and args:
            harness = args.pop(0)
    terminal_id = os.environ.get("SUPERSET_TERMINAL_ID", "")
    workspace_id = os.environ.get("SUPERSET_WORKSPACE_ID", "")
    if not terminal_id or not workspace_id:
        return 0

    data, raw = read_stdin()
    debug("event=%s harness=%s terminal=%s stdin=%s" % (event, harness, terminal_id, raw.strip()[:2000]))
    if not data:
        return 0  # empty or malformed payload: nothing to name the tab after

    dry = os.environ.get("SUPERSET_TAB_TITLE_DRY_RUN") == "1"
    spath = state_path(terminal_id)
    state = load_state(spath) if spath else {}

    if dry:
        existing = os.environ.get("SUPERSET_TAB_TITLE_EXISTING", "")
        preferred = None
    else:
        existing, preferred = current_custom_title(terminal_id, workspace_id)
        if existing is None:
            # Host unreachable or terminal unknown; fall back to what we last set.
            existing = state.get("last_title", "")

    title, new_state = decide(event, harness, data, existing, state)
    if title is None or title == existing:
        save_state(spath, new_state)
        return 0
    if dry:
        sys.stdout.write(title + "\n")
    elif not rename(terminal_id, workspace_id, title, preferred):
        return 0
    new_state["last_title"] = title
    save_state(spath, new_state)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception as exc:  # noqa: BLE001 — never block a prompt or a stop
        debug("crashed: %r" % (exc,))
        sys.exit(0)
