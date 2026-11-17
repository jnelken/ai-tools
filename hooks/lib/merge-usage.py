#!/usr/bin/env python3
"""Merge one day's usage bundles from several machines into one, for the ccusage post.

A bundle is what `post-yesterdays-ccusage.sh --collect` builds for one day:
  {"claude": <ccusage claude daily --json>, "codex": <ccusage codex daily --json>,
   "effort": <effort-mix.py>, "codex_effort": <effort-mix.py --codex>}

Usage: merge-usage.py BUNDLE.json [BUNDLE.json ...]
Prints one bundle of the same shape, so the hook renders the merged day exactly
as it renders a single machine's.

       merge-usage.py --frame   < json     (on a peer: wrap JSON for a terminal screen)
       merge-usage.py --unframe < screen   (on the poster: the JSON back, or exit 1)
A frame is gzip + base64 between marker lines, ending in the base64's sha256, so
a screen that dropped or mangled any part of it is rejected rather than misread.

ccusage numbers add: day totals, cache, and per-model rows (Claude
`modelBreakdowns` by `modelName`, Codex `models` by key). Effort percentages do
not add. Each machine's row stands for its weight (dollars for Claude, token
weight for Codex, see effort-mix.py), so a merged row is the weight-averaged
split and the total row is weighted across every model on every machine, the
same rule effort-mix.py uses within one machine.
"""
import base64
import gzip
import hashlib
import json
import sys

LEVEL_ORDER = ["Min", "Low", "Med", "High", "XHigh", "Max"]


def is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def merge_values(a, b):
    if a is None:
        return b
    if b is None:
        return a
    if is_num(a) and is_num(b):
        return a + b
    if isinstance(a, dict) and isinstance(b, dict):
        out = dict(a)
        for k, v in b.items():
            out[k] = merge_values(out.get(k), v)
        return out
    if isinstance(a, list) and isinstance(b, list):
        if all(isinstance(x, dict) and "modelName" in x for x in a + b):
            by_name = {}
            for x in a + b:
                by_name[x["modelName"]] = merge_values(by_name.get(x["modelName"]), x)
            return list(by_name.values())
        return a + [x for x in b if x not in a]
    return a


def day_row(report):
    """The single day row of a ccusage report, or None ({} / [] / no data)."""
    daily = report.get("daily") if isinstance(report, dict) else report
    if isinstance(daily, list) and daily and isinstance(daily[0], dict) and daily[0]:
        return daily[0]
    return None


def merge_report(reports):
    rows = [r for r in (day_row(x) for x in reports) if r is not None]
    if not rows:
        return []
    merged = rows[0]
    for r in rows[1:]:
        merged = merge_values(merged, r)
    return {"daily": [merged]}


def whole_percents(shares):
    """Largest-remainder rounding so a row's percentages add to exactly 100."""
    raw = [s * 100 for s in shares]
    floors = [int(r) for r in raw]
    order = sorted(range(len(raw)), key=lambda i: raw[i] - floors[i], reverse=True)
    for i in order[: 100 - sum(floors)]:
        floors[i] += 1
    return floors


def merge_effort(efforts):
    efforts = [e for e in efforts if isinstance(e, dict) and e.get("levels") and e.get("rows")]
    if not efforts:
        return {}
    used = {lv for e in efforts for lv in e["levels"]}
    levels = [lv for lv in LEVEL_ORDER if lv in used] + sorted(used - set(LEVEL_ORDER))

    # {model: [(weight, {level: share})]} from every machine's rows.
    parts = {}
    for e in efforts:
        weights = e.get("weights") or {}
        for model, pcts in e["rows"].items():
            split = {lv: p / 100 for lv, p in zip(e["levels"], pcts)}
            parts.setdefault(model, []).append((weights.get(model, 0) or 0, split))

    rows, shares_by_model, weights = {}, {}, {}
    for model, entries in parts.items():
        w = sum(x for x, _ in entries)
        if w > 0:
            shares = [sum(x * s.get(lv, 0) for x, s in entries) / w for lv in levels]
        else:  # no weight known on any machine: count each machine equally
            shares = [sum(s.get(lv, 0) for _, s in entries) / len(entries) for lv in levels]
        rows[model], shares_by_model[model], weights[model] = whole_percents(shares), shares, w

    total_w = sum(weights.values())
    if total_w > 0:
        total = [sum(weights[m] * sh[i] for m, sh in shares_by_model.items()) / total_w for i in range(len(levels))]
    else:  # nothing weighted: every model counts equally, as effort-mix.py does
        total = [sum(sh[i] for sh in shares_by_model.values()) / len(shares_by_model) for i in range(len(levels))]
    return {"levels": levels, "rows": rows, "total": whole_percents(total), "weights": weights}


def main(paths):
    bundles = []
    for p in paths:
        with open(p) as f:
            b = json.load(f)
        if isinstance(b, dict):
            bundles.append(b)
    return {
        "claude": merge_report([b.get("claude") for b in bundles]),
        "codex": merge_report([b.get("codex") for b in bundles]),
        "effort": merge_effort([b.get("effort") for b in bundles]),
        "codex_effort": merge_effort([b.get("codex_effort") for b in bundles]),
    }


BEGIN, END = "CCUSAGE-BUNDLE-BEGIN", "CCUSAGE-BUNDLE-END"


def frame(text):
    """JSON text -> lines a terminal can show: gzip, base64, and the base64 hash."""
    b64 = base64.b64encode(gzip.compress(text.encode())).decode()
    return f"{BEGIN}\n{b64}\n{END} {hashlib.sha256(b64.encode()).hexdigest()}"


def unframe(screen):
    """A terminal screen holding one frame -> the JSON text, or raise. The base64
    line comes back wrapped at the terminal width, so the rows between the markers
    are joined with whitespace removed (base64 has none of its own)."""
    lines = [ln.strip() for ln in screen.splitlines()]
    end = max(i for i, ln in enumerate(lines) if ln.startswith(END + " "))
    begin = max(i for i, ln in enumerate(lines[:end]) if ln == BEGIN)
    b64 = "".join("".join(ln.split()) for ln in lines[begin + 1 : end])
    if hashlib.sha256(b64.encode()).hexdigest() != lines[end].split()[1]:
        raise ValueError("frame hash mismatch")
    data = json.loads(gzip.decompress(base64.b64decode(b64)))
    if not isinstance(data, dict):
        raise ValueError("frame is not a JSON object")
    return json.dumps(data)


if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["--frame"]:
        print(frame(sys.stdin.read()))
    elif args[:1] == ["--unframe"]:
        try:
            print(unframe(sys.stdin.read()))
        except Exception as exc:  # noqa: BLE001 — any bad screen is one failure
            print(f"unframe: {exc!r}", file=sys.stderr)
            sys.exit(1)
    else:
        print(json.dumps(main(args)))
