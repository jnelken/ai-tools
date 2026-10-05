#!/usr/bin/env python3
"""Per-model token accounting for advance-roadmap runs.

Every model call a run makes (orchestrator, worker, cross-model reviewer) appends
one line to <run dir>/model-usage.jsonl; runrecord.py folds that file into the
runs.jsonl row and gen-dashboard.py renders it. Nothing here gates routing — that
is usage.py's job (quota percentages) — this only records what a run consumed.

  tokens.py claude-json  IN OUT_TEXT --role R --log FILE   # claude -p --output-format json
  tokens.py cursor-json  IN OUT_TEXT --role R --log FILE --model M   # agent --output-format json
  tokens.py codex        IN --role R --model M --log FILE  # codex exec stdout (merged stderr)

The *-json forms also write the model's final message to OUT_TEXT, so callers keep
their plain-text pipeline (limit-banner grep, fence extraction). If IN isn't the
expected JSON (a crash, a banner) it is copied to OUT_TEXT verbatim and nothing is
logged. Every command exits 0: accounting must never fail a run.

Record fields (token counts are disjoint; total = input + cache_write + output, i.e. what the model
newly processed — cache_read is kept apart because it dwarfs the rest and is billed far lower.
Codex's own "tokens used" line excludes cached input the same way):
  at, role, round (review round N, else null), provider, model, input (uncached), cache_read, cache_write, output,
  total, cost_usd (Claude reports it; null elsewhere), source
Codex's `output` includes reasoning tokens, as its own total does.
"""
import argparse
import glob
import json
import os
import re
import shutil
import sys
import time

CODEX_SESSIONS = os.environ.get("ADVANCE_ROADMAP_CODEX_SESSIONS", os.path.expanduser("~/.codex/sessions"))
FIELDS = ("input", "cache_read", "cache_write", "output")


def num(v):
    return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0


def record(role, provider, model, input_, cache_read, cache_write, output, cost, source, rnd=None):
    r = {"at": int(time.time()), "role": role, "round": rnd, "provider": provider, "model": model,
         "input": num(input_), "cache_read": num(cache_read), "cache_write": num(cache_write),
         "output": num(output),
         "cost_usd": round(cost, 4) if isinstance(cost, (int, float)) else None,
         "source": source}
    r["total"] = r["input"] + r["cache_write"] + r["output"]  # newly processed; cache reads are tracked apart
    return r


def append(log, recs):
    recs = [r for r in recs if r["total"] > 0]
    if not log or not recs:
        return
    try:
        os.makedirs(os.path.dirname(os.path.abspath(log)), exist_ok=True)
        with open(log, "a", encoding="utf-8") as f:
            f.write("".join(json.dumps(r) + "\n" for r in recs))
    except OSError:
        pass


def load_result(path):
    """(result object, text printed around it) from a CLI's --output-format json stdout.

    The object is a single JSON line; stderr is merged into the same file, so warnings may
    surround it. Returns (None, "") when no such line exists.
    """
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except OSError:
        return None, ""
    for i in range(len(lines) - 1, -1, -1):
        if not lines[i].startswith("{"):
            continue
        try:
            v = json.loads(lines[i])
        except ValueError:
            continue
        if isinstance(v, dict) and "result" in v:
            return v, "\n".join(lines[:i] + lines[i + 1:]).strip()
    return None, ""


def write_text(path, noise, result):
    with open(path, "w", encoding="utf-8") as f:
        f.write((noise + "\n" if noise else "") + str(result or "") + "\n")


def passthrough(src, dst):
    try:
        shutil.copyfile(src, dst)
    except OSError:
        pass


def summarize(path):
    """Fold a run's model-usage.jsonl into {total, input, cache_read, cache_write, output,
    cost_usd, calls}. `calls` has one row per (role, round, provider, model) — a review round
    is one row per reviewer — in first-seen order. None when nothing was logged."""
    rows = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if isinstance(r, dict):
                    rows.append(r)
    except (OSError, TypeError):
        return None
    if not rows:
        return None
    calls = {}
    for r in rows:
        key = (r.get("role"), r.get("round"), r.get("provider"), r.get("model"))
        c = calls.setdefault(key, {"role": key[0], "round": key[1], "provider": key[2], "model": key[3],
                                   "input": 0, "cache_read": 0, "cache_write": 0, "output": 0,
                                   "total": 0, "cost_usd": None})
        for k in FIELDS + ("total",):
            c[k] += num(r.get(k))
        if isinstance(r.get("cost_usd"), (int, float)):
            c["cost_usd"] = round((c["cost_usd"] or 0) + r["cost_usd"], 4)
    out = {k: sum(c[k] for c in calls.values()) for k in FIELDS + ("total",)}
    costs = [c["cost_usd"] for c in calls.values() if c["cost_usd"] is not None]
    out["cost_usd"] = round(sum(costs), 4) if costs else None
    out["calls"] = list(calls.values())
    return out


def cmd_claude_json(a):
    data, noise = load_result(a.input)
    if not data:
        return passthrough(a.input, a.out_text)
    write_text(a.out_text, noise, data.get("result"))
    # modelUsage has one entry per model the session touched, subagents included.
    mu = data.get("modelUsage") or {}
    recs = [record(a.role, "claude", model, u.get("inputTokens"), u.get("cacheReadInputTokens"),
                   u.get("cacheCreationInputTokens"), u.get("outputTokens"), u.get("costUSD"), "claude-json", a.round)
            for model, u in mu.items() if isinstance(u, dict)]
    if not recs and isinstance(data.get("usage"), dict):  # older CLI: session-wide, model unknown
        u = data["usage"]
        recs = [record(a.role, "claude", a.model or "claude", u.get("input_tokens"),
                       u.get("cache_read_input_tokens"), u.get("cache_creation_input_tokens"),
                       u.get("output_tokens"), data.get("total_cost_usd"), "claude-json", a.round)]
    append(a.log, recs)


def cmd_cursor_json(a):
    data, noise = load_result(a.input)
    if not data:
        return passthrough(a.input, a.out_text)
    write_text(a.out_text, noise, data.get("result"))
    u = data.get("usage") or {}
    # Cursor reports the pool ("auto"), not the model it routed to.
    append(a.log, [record(a.role, "cursor", a.model or "auto", u.get("inputTokens"), u.get("cacheReadTokens"),
                          u.get("cacheWriteTokens"), u.get("outputTokens"), None, "cursor-json", a.round)])


def codex_rollout_usage(session_id):
    """Final total_token_usage of the rollout for this session id, or None."""
    for path in glob.glob(os.path.join(CODEX_SESSIONS, "*", "*", "*", f"rollout-*{session_id}.jsonl")):
        last = None
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                for line in f:
                    if '"token_count"' not in line:
                        continue
                    try:
                        info = (json.loads(line).get("payload") or {}).get("info") or {}
                    except ValueError:
                        continue
                    if isinstance(info.get("total_token_usage"), dict):
                        last = info["total_token_usage"]
        except OSError:
            continue
        if last:
            return last
    return None


def cmd_codex(a):
    try:
        with open(a.input, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return
    # Header: "session id: <uuid>". The prompt and ORCHESTRATOR.md are echoed after it and
    # may quote older headers, so the first match is this run's.
    m = re.search(r"^session id: ([0-9a-f-]{36})\s*$", text, re.M)
    u = codex_rollout_usage(m.group(1)) if m else None
    if u:
        cached = num(u.get("cached_input_tokens"))
        recs = [record(a.role, "codex", a.model, num(u.get("input_tokens")) - cached, cached,
                       u.get("cache_write_input_tokens"), u.get("output_tokens"), None, "codex-rollout", a.round)]
    else:
        # Fallback: the closing "tokens used\n111,291" line is a total with no split.
        t = re.findall(r"^tokens used\s*\n\s*([\d,]+)\s*$", text, re.M)
        recs = [record(a.role, "codex", a.model, 0, 0, 0, int(t[-1].replace(",", "")), None, "codex-total", a.round)] if t else []
    append(a.log, recs)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in (("claude-json", cmd_claude_json), ("cursor-json", cmd_cursor_json), ("codex", cmd_codex)):
        p = sub.add_parser(name)
        p.add_argument("input")
        if name != "codex":
            p.add_argument("out_text")
        p.add_argument("--role", required=True, choices=["orchestrator", "worker", "reviewer"])
        p.add_argument("--log", required=True)
        p.add_argument("--model", default="")
        p.add_argument("--round", type=int, default=None, help="review round (reviewer role)")
        p.set_defaults(func=fn)
    a = ap.parse_args()
    try:
        a.func(a)
    except Exception as e:  # accounting must never fail a run
        print(f"tokens.py: {type(e).__name__}: {e}", file=sys.stderr)
    sys.exit(0)


if __name__ == "__main__":
    main()
