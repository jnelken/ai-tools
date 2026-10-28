#!/usr/bin/env python3
"""Split one day's Claude or Codex spend by reasoning-effort level, for the ccusage post.

`ccusage claude daily --json` has no effort field, but Claude Code stamps every
assistant turn in its transcripts (`effort` / `perTurnEffort`). This reads those
transcripts and apportions each model's ccusage cost across the effort levels
used that day, so the table's Cost column stays exactly ccusage's number and
only the within-model split is ours.

Codex rollouts record the effort on each turn too, so `--codex` does the same for
`ccusage codex daily --json`. That report has no per-model cost, so a model's
weight there is its own token weight and the total row follows the token mix.

Usage: effort-mix.py [--codex] YYYY-MM-DD < ccusage-daily.json
Prints one JSON object on stdout:
  {"levels": ["Med", "High"],                    # only levels used that day
   "rows":   {"claude-opus-5-5": [10, 90], ...}, # whole-number %, sums to 100
   "total":  [14, 86]}                           # cost-weighted across models
or {} when there is nothing to report. Rows for models with no effort data are
left out, so the caller prints those without effort cells rather than "0%".

The split is an estimate: a turn's weight is its tokens times Anthropic's usual
price ratios (input 1, output 5, 5m cache write 1.25, 1h write 2, cache read
0.1), not the exact per-model price. That only skews the split inside a model.
Exit status is always 0; any failure prints {} so the post goes out unchanged.
"""
import datetime
import glob
import json
import os
import sys

# Display order, lowest first; labels are 5 characters or fewer to keep columns narrow.
LEVELS = [("minimal", "Min"), ("low", "Low"), ("medium", "Med"), ("high", "High"), ("xhigh", "XHigh"), ("max", "Max")]

W_INPUT, W_OUTPUT, W_CACHE_WRITE_5M, W_CACHE_WRITE_1H, W_CACHE_READ = 1.0, 5.0, 1.25, 2.0, 0.1


def config_roots():
    env = os.environ.get("CLAUDE_CONFIG_DIR", "").strip()
    if env:
        return [p.strip() for p in env.split(",") if p.strip()]
    home = os.path.expanduser("~")
    return [os.path.join(home, ".config", "claude"), os.path.join(home, ".claude")]


def weight(u):
    """Cost-proportional weight of one usage block."""
    cc = u.get("cache_creation") or {}
    w5 = cc.get("ephemeral_5m_input_tokens")
    w1 = cc.get("ephemeral_1h_input_tokens")
    if w5 is None and w1 is None:
        w5, w1 = u.get("cache_creation_input_tokens", 0) or 0, 0
    return (
        (u.get("input_tokens") or 0) * W_INPUT
        + (u.get("output_tokens") or 0) * W_OUTPUT
        + (w5 or 0) * W_CACHE_WRITE_5M
        + (w1 or 0) * W_CACHE_WRITE_1H
        + (u.get("cache_read_input_tokens") or 0) * W_CACHE_READ
    )


def effort_weights(day):
    """{model: {effort: weight}} for assistant turns that started on local `day`."""
    day_start = datetime.datetime.strptime(day, "%Y-%m-%d").timestamp()
    out = {}
    seen = set()
    for root in config_roots():
        for path in glob.glob(os.path.join(root, "projects", "**", "*.jsonl"), recursive=True):
            try:
                if os.path.getmtime(path) < day_start:  # untouched since before the day began
                    continue
                handle = open(path, errors="ignore")
            except OSError:
                continue
            with handle:
                for line in handle:
                    if '"usage"' not in line:
                        continue
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        continue
                    msg = rec.get("message")
                    if rec.get("type") != "assistant" or not isinstance(msg, dict):
                        continue
                    usage, model = msg.get("usage"), msg.get("model")
                    effort = rec.get("perTurnEffort") or rec.get("effort")
                    if not usage or not model or model == "<synthetic>" or not effort:
                        continue
                    try:
                        when = datetime.datetime.fromisoformat(rec["timestamp"].replace("Z", "+00:00"))
                    except (KeyError, ValueError):
                        continue
                    if when.astimezone().strftime("%Y-%m-%d") != day:
                        continue
                    # One request is written to the transcript several times (one record
                    # per content block, plus resumed sessions); count it once, like ccusage.
                    key = (msg.get("id"), rec.get("requestId"))
                    if None not in key:
                        if key in seen:
                            continue
                        seen.add(key)
                    # The turn's own usage covers its "message" iterations. An advisor call
                    # is billed to the advisor's model, which ccusage lists as its own row.
                    by_model = {model: weight(usage)}
                    for it in usage.get("iterations") or []:
                        if it.get("type") == "advisor_message" and it.get("model"):
                            by_model[it["model"]] = by_model.get(it["model"], 0.0) + weight(it)
                    for m, w in by_model.items():
                        bucket = out.setdefault(m, {})
                        bucket[effort] = bucket.get(effort, 0.0) + w
    return out


def whole_percents(shares):
    """Largest-remainder rounding so a row's percentages add to exactly 100."""
    raw = [s * 100 for s in shares]
    floors = [int(r) for r in raw]
    order = sorted(range(len(raw)), key=lambda i: raw[i] - floors[i], reverse=True)
    for i in order[: 100 - sum(floors)]:
        floors[i] += 1
    return floors


def codex_roots():
    return [os.path.join(os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex"), "sessions")]


def codex_weight(delta):
    """Token weight of one Codex usage delta (input includes cached; output includes reasoning)."""
    cached = delta.get("cached_input_tokens") or 0
    return (
        ((delta.get("input_tokens") or 0) - cached) * W_INPUT
        + cached * W_CACHE_READ
        + (delta.get("output_tokens") or 0) * W_OUTPUT
    )


USAGE_FIELDS = ("input_tokens", "cached_input_tokens", "output_tokens")


def codex_effort_weights(day):
    """{model: {effort: weight}} for token usage that landed on local `day`.

    A rollout stamps each turn_context with the model and reasoning effort; the
    token_count events that follow belong to that turn. total_token_usage is
    cumulative and token_count events repeat, so only growth in the total counts.
    """
    day_start = datetime.datetime.strptime(day, "%Y-%m-%d").timestamp()
    out = {}
    for root in codex_roots():
        for path in glob.glob(os.path.join(root, "**", "rollout-*.jsonl"), recursive=True):
            try:
                if os.path.getmtime(path) < day_start:
                    continue
                handle = open(path, errors="ignore")
            except OSError:
                continue
            model = effort = None
            prev = {k: 0 for k in USAGE_FIELDS}
            with handle:
                for line in handle:
                    is_context = '"turn_context"' in line
                    if not is_context and '"token_count"' not in line:
                        continue
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        continue
                    payload = rec.get("payload") or {}
                    if is_context:
                        mode = (payload.get("collaboration_mode") or {}).get("settings") or {}
                        model = payload.get("model") or mode.get("model") or model
                        effort = payload.get("effort") or mode.get("reasoning_effort") or effort
                        continue
                    info = payload.get("info") if payload.get("type") == "token_count" else None
                    total = (info or {}).get("total_token_usage")
                    if not total:
                        continue
                    delta = {k: (total.get(k) or 0) - prev[k] for k in USAGE_FIELDS}
                    if any(v < 0 for v in delta.values()):  # the counter reset; trust the last request
                        delta = {k: (info.get("last_token_usage") or {}).get(k) or 0 for k in USAGE_FIELDS}
                    prev = {k: total.get(k) or 0 for k in USAGE_FIELDS}
                    if not model or not effort or not any(delta.values()):
                        continue
                    try:
                        when = datetime.datetime.fromisoformat(rec["timestamp"].replace("Z", "+00:00"))
                    except (KeyError, ValueError):
                        continue
                    if when.astimezone().strftime("%Y-%m-%d") != day:
                        continue
                    bucket = out.setdefault(model, {})
                    bucket[effort] = bucket.get(effort, 0.0) + codex_weight(delta)
    return out


def build(per_model, costs):
    """Turn {model: {effort: weight}} plus per-model dollars into the printed shape."""
    per_model = {m: w for m, w in per_model.items() if m in costs and sum(w.values()) > 0}
    if not per_model:
        return {}
    used = {e for w in per_model.values() for e in w}
    known = [(k, label) for k, label in LEVELS if k in used]
    extra = sorted(used - {k for k, _ in LEVELS})
    columns = known + [(k, k[:5].capitalize()) for k in extra]

    shares = {m: [w.get(k, 0.0) / sum(w.values()) for k, _ in columns] for m, w in per_model.items()}
    rows = {m: whole_percents(s) for m, s in shares.items()}

    priced = sum(costs[m] for m in per_model)
    if priced > 0:
        total_shares = [sum(costs[m] * shares[m][i] for m in per_model) / priced for i in range(len(columns))]
    else:  # nothing priced; weight every model equally
        total_shares = [sum(shares[m][i] for m in per_model) / len(per_model) for i in range(len(columns))]
    return {"levels": [label for _, label in columns], "rows": rows, "total": whole_percents(total_shares)}


def main():
    args = sys.argv[1:]
    codex = "--codex" in args
    day = [a for a in args if a != "--codex"][0]
    cc = json.load(sys.stdin)
    daily = cc.get("daily") if isinstance(cc, dict) else cc
    first = (daily[0] if daily else None) or {}
    if codex:
        weights = codex_effort_weights(day)
        # No per-model cost in ccusage codex: a model counts by its own token weight.
        costs = {m: sum(weights.get(m, {}).values()) for m in (first.get("models") or {})}
        return build(weights, costs)
    costs = {b["modelName"]: b.get("cost") or 0.0 for b in (first.get("modelBreakdowns") or []) if b.get("modelName")}
    return build(effort_weights(day), costs)


if __name__ == "__main__":
    try:
        print(json.dumps(main()))
    except Exception:  # never break the post over a decoration
        print("{}")
