#!/usr/bin/env python3
"""Optional read-only model analyst for advance-roadmap follow-ups (DEV-242 slice 3c).

After deterministic recognition (followscan), optionally ask Codex or Claude to surface more
findings from the same bounded corpus. Findings are kept only when every cited quote is verified
in an allowed corpus file, and only when they appear in the provider's final answer (never the
transcript, which echoes the prompt's example fence). Disabled with ADVANCE_ROADMAP_FOLLOWUP_ANALYST=0;
skipped when gated, unavailable, malformed, or timed out — deterministic findings still stand.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import followrepair  # noqa: E402
import followscan  # noqa: E402
import procrun  # noqa: E402
import usage as usagelib  # noqa: E402

ANALYST_FENCE = "FOLLOWUP_FINDINGS_JSON"
ANALYST_TIMEOUT_S = float(os.environ.get("ADVANCE_ROADMAP_FOLLOWUP_ANALYST_TIMEOUT_S", "300"))
TITLE_SAFE = re.compile(r"[^\w\s.,;:!?()\[\]/'\"%\-]+", re.UNICODE)
TOKENS_TIMEOUT_S = 60
# Cap inlined corpus bytes so the prompt stays bounded (injection surface is the paste, not FS).
CORPUS_PROMPT_MAX_BYTES = int(os.environ.get("ADVANCE_ROADMAP_FOLLOWUP_ANALYST_CORPUS_BYTES", "200000"))
CODEX = os.environ.get("ADVANCE_ROADMAP_CODEX_BIN", "/opt/homebrew/bin/codex")
CLAUDE = os.environ.get("ADVANCE_ROADMAP_CLAUDE_BIN", "/Users/jake/.local/bin/claude")
PROVIDERS = ("codex", "claude")
ALLOWED_KINDS = frozenset({
    "wrong-knowledge", "misfiring-automation", "repeated-manual-work",
    "fragile-tooling", "misleading-surface", "newly-dead-mechanisms", "analyst",
})
CLAUDE_DENY_TOOLS = (
    "Read,Edit,Write,NotebookEdit,Bash,MultiEdit,Glob,Grep,"
    "WebFetch,WebSearch,Task,Agent,SendMessage,ListMcpResources,ReadMcpResource"
)
# Codex has no --tools "" equivalent; disable the features that expose shell/FS/browser tools.
# (sandbox_permissions is unrecognized by current Codex — do not rely on it.)
CODEX_DISABLE_FEATURES = (
    "shell_tool",
    "unified_exec",
    "browser_use",
    "computer_use",
    "apps",
)


def enabled():
    return os.environ.get("ADVANCE_ROADMAP_FOLLOWUP_ANALYST", "1") == "1"


def corpus_stamps(st):
    """Stamp set for stamp-scoped evidence checks (runs.jsonl / verdicts)."""
    stamp = st.get("stamp")
    if stamp:
        return [stamp]
    children = st.get("children") or []
    return list(children) if children else None


def _resolve_source(source):
    """Resolved path string, or None when the value is not a usable path string."""
    if not isinstance(source, str) or not source.strip():
        return None
    try:
        return str(Path(source).resolve())
    except (OSError, TypeError, ValueError):
        return None


def corpus_label(index, path):
    """Opaque prompt/sandbox path for a corpus file — never an absolute host path."""
    name = Path(path).name or "file"
    return f"corpus/{int(index):03d}-{name}"


def empty_analyst_cwd(st):
    """Fresh stage-local cwd for provider invokes (cleared before each analyze)."""
    sandbox = followrepair.stage_dir(st["key"]) / "corpus-sandbox"
    if sandbox.exists():
        shutil.rmtree(sandbox)
    sandbox.mkdir(parents=True)
    return sandbox


def prepare_analyst_sandbox(st, pasted):
    """Materialize only the labeled corpus under the sandbox cwd.

    Invariant owner for filesystem isolation at the provider invoke: run_analyst (via this
    prepare + empty-of-host-files cwd). Absolute host paths never appear as sandbox entries;
    the prompt cites the same opaque labels. Claude additionally has no file-read tools.
    """
    sandbox = empty_analyst_cwd(st)
    for label, body in (pasted or {}).items():
        if not isinstance(label, str) or not label.startswith("corpus/") or "/" in label[7:]:
            continue
        dest = sandbox / label
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(body if isinstance(body, str) else "", encoding="utf-8")
    return sandbox


def analyze(st, findings, files):
    """Optional read-only model pass. Returns (kept_findings, rejected_non_findings).

    Off / empty corpus → ([], []). Quota, timeout, crash, or missing fence leave deterministic
    findings untouched and record st['analyst'].

    Invariant — bounded corpus to the model network call (paths: analyze→run_analyst→Codex/Claude).
    Owner: analyze prepares opaque labels + sandbox via prepare_analyst_sandbox / inline_corpus;
    run_analyst is the only invoke entry and must receive that sandbox.
    """
    if not enabled() or not files:
        return [], []
    # Stage state.json can contain prior analyst prose — never cite it as evidence.
    files = [Path(f).resolve() for f in files if Path(f).name != "state.json"]
    if not files:
        st["analyst"] = "skipped: no citable corpus files"
        return [], []
    prompt = analyst_prompt(st, findings, files)
    try:
        sandbox = prepare_analyst_sandbox(st, st.get("_analyst_paste") or {})
    except (OSError, ValueError, TypeError) as e:
        # Optional analyst must never abort deterministic scan/recording.
        st["analyst"] = f"failed: sandbox prepare: {e}"
        st.pop("_analyst_paste", None)
        st.pop("_analyst_label_map", None)
        return [], []
    try:
        # Invariant (owner: analyze gate check): every path to run_analyst must see an
        # available gate; an unreadable gate (None) is unavailable — fail closed.
        # Dry-run owner: gate_one(..., readonly=True) — load saved state, never refresh/save.
        limited, errored = [], []
        provider = None
        answer = ""
        for provider in PROVIDERS:
            gate = followrepair.gate_one("analyst", provider, readonly=bool(st.get("dry_run")))
            st.setdefault("usage_snapshots", []).append({
                "at": followrepair.now_iso(), "role": "analyst", "provider": provider, "gate": gate})
            if gate is None or not gate.get("available"):
                continue
            try:
                transcript, answer, exit_code, provider_result = run_analyst(
                    st, provider, prompt, sandbox)
            except (OSError, subprocess.TimeoutExpired) as e:
                # Launch/timeout on one provider must not skip an available fallback.
                errored.append(provider)
                st.setdefault("analyst_errors", []).append(
                    {"provider": provider, "at": followrepair.now_iso(), "error": f"{type(e).__name__}: {e}"})
                continue
            # Invariant (owner: live_limit_banner + paste scrub): gate only on a live CLI limit.
            # The analyst prompt inlines the corpus, so transcripts echo quoted banners; strip those
            # before detection. Aggressive (line/substring) scrub only on exit 0 — a real CLI limit
            # still prints its banner outside the paste and must remain visible when exit != 0.
            scrubbed = _transcript_without_paste(
                st, transcript, fragments=(exit_code == 0))
            banner = usagelib.live_limit_banner(
                scrubbed, exit_code=exit_code, final_answer=answer, provider_result=provider_result)
            if banner:
                record_analyst_limit(st, provider, banner)
                limited.append(provider)
                continue
            # Require a successful invoke before parsing findings (no leftover -o on a failed exit).
            if exit_code != 0 or usagelib.provider_is_error(provider_result):
                errored.append(provider)
                continue
            break
        else:
            if limited and not errored:
                st["analyst"] = (
                    f"skipped: {' and '.join(limited)} hit "
                    f"{'their limits' if len(limited) > 1 else 'its limit'}")
            elif errored:
                bits = []
                if limited:
                    bits.append(f"{' and '.join(limited)} hit limit")
                bits.append(f"{' and '.join(errored)} failed")
                st["analyst"] = "failed: " + "; ".join(bits)
            else:
                st["analyst"] = "skipped: no analyst under its usage gate"
            return [], []
        # Findings come only from the final answer: the transcript echoes the prompt's example fence.
        m = re.findall(r"```" + ANALYST_FENCE + r"\s*\n(.*?)\n```", answer or "", re.S)
        try:
            doc = json.loads(m[-1]) if m else {}
        except ValueError:
            doc = {}
        st["analyst"] = f"{provider}: {'answered' if m else 'no findings block'}"
        return accept_findings(
            st, findings, files, doc if isinstance(doc, dict) else {}, provider=provider)
    finally:
        st.pop("_analyst_paste", None)
        st.pop("_analyst_label_map", None)


def _evidence_with_real_sources(raw_ev, label_map):
    """Rewrite opaque corpus labels to real host paths for verification/persistence."""
    out = []
    for e in raw_ev or []:
        if not isinstance(e, dict):
            out.append(e)
            continue
        src = e.get("source")
        if isinstance(src, str) and src in label_map:
            out.append({**e, "source": label_map[src]})
        else:
            out.append(e)
    return out


def accept_findings(st, findings, files, doc, provider=None):
    """Keep analyst findings whose every quote verifies in the corpus; reject the rest.

    Malformed collection types abort analyst output without raising, preserving deterministic findings.
    """
    if not isinstance(doc, dict):
        if provider:
            st["analyst"] = f"{provider}: malformed findings block"
        return [], []
    raw_findings, raw_non = doc.get("findings"), doc.get("non_findings")
    if raw_findings is None:
        raw_findings = []
    if raw_non is None:
        raw_non = []
    if not isinstance(raw_findings, list) or not isinstance(raw_non, list):
        if provider:
            st["analyst"] = f"{provider}: malformed findings block"
        return [], []
    known = {f["id"] for f in findings}
    stamps = corpus_stamps(st)
    label_map = st.get("_analyst_label_map") or {}
    paste = st.get("_analyst_paste") or {}
    kept, rejected = [], []
    for raw in raw_findings:
        if not isinstance(raw, dict):
            continue
        title = raw.get("title")
        if not isinstance(title, str) or not title.strip():
            continue
        # Free text is capped; kind is allowlisted; key is derived from verified evidence.
        kind_raw = raw.get("kind") if isinstance(raw.get("kind"), str) else ""
        kind = kind_raw if kind_raw in ALLOWED_KINDS else "analyst"
        title = TITLE_SAFE.sub("", title.strip())[:200].strip()
        if not title:
            continue
        fix = TITLE_SAFE.sub("", (raw.get("fix") if isinstance(raw.get("fix"), str) else ""))[:400].strip()
        raw_ev = raw.get("evidence") or []
        # Every evidence entry must be a dict that verifies — drop the whole finding otherwise.
        if not isinstance(raw_ev, list) or not raw_ev or not all(isinstance(e, dict) for e in raw_ev):
            rejected.append(followscan.non_finding(
                title, "rejected: its evidence is not in the corpus",
                _safe_evidence(
                    [e for e in raw_ev if isinstance(e, dict)] if isinstance(raw_ev, list) else [])))
            continue
        # Paste check against opaque labels (what the prompt exposed) before remapping.
        if paste:
            ok_paste = True
            for e in raw_ev:
                src = e.get("source")
                body = paste.get(src) if isinstance(src, str) else None
                if body is None or not quote_in_paste(e.get("quote"), body):
                    ok_paste = False
                    break
            if not ok_paste:
                rejected.append(followscan.non_finding(
                    title, "rejected: its evidence is not in the corpus", _safe_evidence(raw_ev)))
                continue
        mapped_ev = _evidence_with_real_sources(raw_ev, label_map)
        if any(_resolve_source(e.get("source")) is None for e in mapped_ev):
            rejected.append(followscan.non_finding(
                title, "rejected: its evidence is not in the corpus", _safe_evidence(raw_ev)))
            continue
        if not all(followscan.evidence_holds(e, files, stamps) for e in mapped_ev):
            rejected.append(followscan.non_finding(
                title, "rejected: its evidence is not in the corpus", _safe_evidence(raw_ev)))
            continue
        try:
            ev_objs = [followscan.evidence(e["source"], e["quote"]) for e in mapped_ev]
        except (OSError, TypeError, ValueError):
            # evidence_holds is whitespace-normalized; evidence() requires an exact span.
            rejected.append(followscan.non_finding(
                title, "rejected: its evidence is not in the corpus", _safe_evidence(raw_ev)))
            continue
        # Key from first verified evidence — not model-supplied — so finding_id can't forge tickets.
        first = ev_objs[0]
        key = f"{Path(first['source']).name}:{first['quote'][:80]}"
        f = followscan.finding(st["stage"], st["repo"], kind, title, key, ev_objs, fix)
        # Provenance: analyst findings ticket/report only — never autonomous repair (followup.repair_todo).
        f["source"] = "analyst"
        if f["id"] not in known:
            kept.append(f)
            known.add(f["id"])
    for raw in raw_non:
        if isinstance(raw, dict) and isinstance(raw.get("title"), str) and raw.get("title").strip():
            title = TITLE_SAFE.sub("", raw["title"].strip())[:200].strip()
            if not title:
                continue
            why = TITLE_SAFE.sub(
                "", (raw.get("why") if isinstance(raw.get("why"), str) else ""))[:400].strip()
            rejected.append(followscan.non_finding(title, why, []))
    return kept, rejected


def _safe_evidence(ev):
    """Cap model-controlled evidence fields persisted on rejection rows."""
    out = []
    for e in ev or []:
        if not isinstance(e, dict):
            continue
        src, quote = e.get("source"), e.get("quote")
        if not isinstance(src, str) or not isinstance(quote, str):
            continue
        out.append({"source": src[:500], "quote": quote[:500]})
    return out


def run_analyst(st, provider, prompt, sandbox):
    """(transcript, final answer, exit code, provider_result) of one read-only analyst call.

    Findings come only from the final answer (Codex's -o file, Claude's JSON result). Live limits
    use usage.live_limit_banner — exit status and provider error records, never a quoted banner alone.
    Process-group deadlines via procrun so a timeout never leaves descendants alive.

    Invariant owner for the model network call: this function. Callers must pass the sandbox from
    prepare_analyst_sandbox (opaque corpus labels only). Codex runs read-only with -C sandbox;
    Claude gets no file-read tools. Prompt paths are labels, never host absolutes.
    """
    d = followrepair.stage_dir(st["key"])
    d.mkdir(parents=True, exist_ok=True)
    sandbox = Path(sandbox).resolve()
    final = d / f"analyst-{provider}.last"
    raw = d / f"analyst-{provider}.log"
    session = d / f"analyst-{provider}.session"
    for f in (final, session):
        f.unlink(missing_ok=True)
    tokens = [sys.executable, str(Path(__file__).resolve().parent / "tokens.py")]
    provider_result = None
    if provider == "codex":
        # Owner of Codex FS isolation: disable shell/exec/browser tools and pin -C to the
        # labeled corpus sandbox. Opaque labels + no shell tools → no host-path reads.
        argv = [CODEX, "exec", "-o", str(final), "-s", "read-only", "-C", str(sandbox),
                "--skip-git-repo-check"]
        for feat in CODEX_DISABLE_FEATURES:
            argv.extend(["--disable", feat])
        argv.append(prompt)
        p = procrun.run(argv, timeout=ANALYST_TIMEOUT_S, text=True, cwd=str(sandbox))
        raw.write_text((p.stdout or "") + (p.stderr or ""))
        procrun.run(
            [*tokens, "codex", str(raw), "--role", "analyst",
             "--log", str(d / "model-usage.jsonl"), "--session-out", str(session)],
            timeout=TOKENS_TIMEOUT_S, text=True)
        transcript = raw.read_text(errors="replace")
    else:
        # Owner of Claude FS isolation: no file-read tools / MCP — answer from the paste only.
        p = procrun.run(
            [CLAUDE, "-p", prompt, "--permission-mode", "plan",
             "--tools", "",
             "--strict-mcp-config", "--mcp-config", "{}",
             "--disallowedTools", CLAUDE_DENY_TOOLS,
             "--output-format", "json"],
            timeout=ANALYST_TIMEOUT_S, text=True, cwd=str(sandbox))
        raw_json = d / "analyst-claude.json"
        raw_json.write_text((p.stdout or "") + (p.stderr or ""))
        procrun.run(
            [*tokens, "claude-json", str(raw_json), str(raw), "--final", str(final), "--role", "analyst",
             "--log", str(d / "model-usage.jsonl"), "--session-out", str(session)],
            timeout=TOKENS_TIMEOUT_S, text=True)
        transcript = followscan.read_text(raw) or raw_json.read_text(errors="replace")
        provider_result = usagelib.load_provider_result(raw_json)
    sid = (followscan.read_text(session) or "").strip()
    if sid:
        st.setdefault("sessions", []).append(
            {"role": "analyst", "provider": provider, "session_id": sid})
    return transcript, followscan.read_text(final) or "", p.returncode, provider_result


def record_analyst_limit(st, provider, text):
    """Keep the hit in the stage first, then gate the provider for everyone else.

    Dry-run scans (st['dry_run']) keep the hit stage-local only — never write the shared
    usage limit store, so `followup.py scan` stays non-mutating outside its temp dir.
    """
    try:
        rc, out = followrepair.usage(
            "parse-limit", "--provider", provider, "--text", text, "--scope", "analyst")
        hit = json.loads(out) if rc == 0 else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        hit = None
    if not isinstance(hit, dict):
        hit = {"provider": provider, "exact_cli_text": text}
    st.setdefault("limits", []).append({"role": "analyst", "at": followrepair.now_iso(), **hit})
    followrepair.write_json(followrepair.stage_dir(st["key"]) / f"analyst-limit-{provider}.json", hit)
    if st.get("dry_run"):
        return
    try:
        followrepair.usage(
            "record-limit", "--provider", provider, "--text", text, "--scope", "analyst")
    except (OSError, subprocess.TimeoutExpired):
        pass


def _utf8_budget_slice(text, max_bytes):
    """Longest prefix of text whose UTF-8 encoding is ≤ max_bytes."""
    if max_bytes <= 0:
        return ""
    raw = text.encode("utf-8")
    if len(raw) <= max_bytes:
        return text
    clipped = raw[:max_bytes]
    while clipped:
        try:
            return clipped.decode("utf-8")
        except UnicodeDecodeError:
            clipped = clipped[:-1]
    return ""


def iter_jsonl_lines_capped(path, max_line_bytes):
    """Yield JSONL text lines, skipping any row whose on-disk length exceeds max_line_bytes.

    Uses readline(size) so one oversized row cannot allocate unbounded memory before the
    stamp filter or corpus byte budget runs.
    """
    limit = max(1, int(max_line_bytes))
    try:
        with open(path, "rb") as f:
            while True:
                raw = f.readline(limit + 1)
                if not raw:
                    break
                if len(raw) > limit and not raw.endswith(b"\n"):
                    # Discard the remainder of this oversized line.
                    while True:
                        more = f.readline(1024 * 1024)
                        if not more or more.endswith(b"\n"):
                            break
                    continue
                yield raw.decode("utf-8", errors="replace").rstrip("\n")
    except OSError:
        return


def _file_text_for_prompt(path, stamps, max_bytes=None):
    """File text for the prompt; runs.jsonl is stamp-filtered when stamps are known.

    Reads through followscan.read_text so large worker logs are capped before paste truncation.
    For stamp-filtered runs.jsonl, stop collecting once max_bytes (UTF-8) is filled so a large
    historical log cannot accumulate unbounded matching rows in memory. Each row is also
    bounded before json.loads so one huge line cannot blow memory during the scan.
    """
    path = Path(path)
    if path.name == "runs.jsonl" and stamps:
        budget = CORPUS_PROMPT_MAX_BYTES if max_bytes is None else max(0, int(max_bytes))
        lines = []
        used = 0
        wanted = set(stamps)
        # Cap a single row at the remaining budget (at least 1) so parse never sees a giant line.
        for line in iter_jsonl_lines_capped(path, max(budget, 1)):
            if used >= budget:
                break
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not (isinstance(row, dict) and row.get("stamp") in wanted):
                continue
            entry = line if line.endswith("\n") else line + "\n"
            n = len(entry.encode("utf-8"))
            if used + n > budget:
                slice_ = _utf8_budget_slice(entry, budget - used)
                if slice_:
                    lines.append(slice_)
                    used = budget
                break
            lines.append(entry)
            used += n
        return "".join(lines)
    return followscan.read_text(path)


def _transcript_without_paste(st, transcript, *, fragments=False):
    """Drop inlined corpus text from a transcript so echoed banners are not live limits.

    Always removes whole pasted bodies. When fragments=True (exit 0), also drops transcript lines
    that equal a paste line or appear as a substring of a paste body — providers often echo one
    corpus line of reasoning. When exit != 0, leave CLI-printed banners that happen to match
    corpus-quoted limit text so a real limit still records.
    """
    text = transcript or ""
    bodies = [b for b in (st.get("_analyst_paste") or {}).values() if b]
    paste_lines = set()
    for body in bodies:
        if body in text:
            text = text.replace(body, "")
        for line in body.splitlines():
            s = line.strip()
            if s:
                paste_lines.add(s)
    if not fragments:
        return text
    kept = []
    for line in text.splitlines():
        s = line.strip()
        if not s:
            kept.append(line)
            continue
        if s in paste_lines or any(s in body for body in bodies):
            continue
        kept.append(line)
    return "\n".join(kept)


def inline_corpus(files, max_bytes=CORPUS_PROMPT_MAX_BYTES, stamps=None):
    """Paste corpus file contents into the prompt.

    Returns (prompt_text, {label: pasted_body}, {label: real_path}).
    Labels are opaque (`corpus/NNN-name`) so absolute host paths never enter the model prompt.
    Budget is measured in UTF-8 bytes (not Python char length).
    """
    parts, used, pasted, label_map = [], 0, {}, {}
    for index, path in enumerate(files, start=1):
        path = Path(path).resolve()
        label = corpus_label(index, path)
        header = f"### {label}\n```\n"
        footer = "\n```\n"
        overhead = len(header.encode("utf-8")) + len(footer.encode("utf-8"))
        room = max_bytes - used - overhead
        if room <= 0:
            # Never push past the advertised UTF-8 cap; omit the notice when it would not fit.
            notice = f"### {label}\n(truncated: corpus budget exhausted)\n"
            if used + len(notice.encode("utf-8")) <= max_bytes:
                parts.append(notice)
            break
        try:
            # Pass room so stamp-filtered runs.jsonl stops collecting once the budget fills.
            text = _file_text_for_prompt(path, stamps, max_bytes=room)
        except OSError:
            # Opaque fixed note only — never paste OSError text (can embed host paths).
            note = f"### {label}\n(unreadable)\n"
            n = len(note.encode("utf-8"))
            if used + n > max_bytes:
                break
            parts.append(note)
            used += n
            continue
        marker = "\n…(truncated)"
        marker_n = len(marker.encode("utf-8"))
        if len(text.encode("utf-8")) <= room:
            body = text
        elif room < marker_n:
            body = _utf8_budget_slice(text, room)
        else:
            body = _utf8_budget_slice(text, room - marker_n) + marker
        pasted[label] = body
        label_map[label] = str(path)
        parts.append(f"{header}{body}{footer}")
        used += len(parts[-1].encode("utf-8"))
    # Parts already end in a newline; do not insert an uncounted separator between files.
    return "".join(parts), pasted, label_map


def quote_in_paste(quote, body):
    """True when the quote appears in the pasted body (whitespace-normalized)."""
    q = " ".join(str(quote or "").split())
    return bool(q) and q in " ".join(str(body or "").split())


def analyst_prompt(st, findings, files):
    stage = st.get("stage") or ""
    cleanup = stage == "project-cleanup"
    scope = (f"Only project-specific bugs in {st.get('repo')} count here."
             if cleanup else
             "Only orchestration, routing, skills, hooks and reporting count here.")
    corpus, pasted, label_map = inline_corpus(files, stamps=corpus_stamps(st))
    st["_analyst_paste"] = pasted
    st["_analyst_label_map"] = label_map
    return f"""You are the read-only analyst for advance-roadmap's {stage} follow-up ({st.get('key')}).
Do not open, search, or read any files on disk outside the labeled corpus paths below.
The entire allowed corpus is pasted below (and mirrored under those labels in your cwd).
Cite evidence only with a corpus/... label from that paste and a verbatim quote from it.
These findings are already recorded (do not repeat them): {json.dumps([f['title'] for f in findings])}

Apply session-hardening's recognition tests (wrong knowledge, misfiring automation, repeated manual
work, fragile tooling, misleading surfaces, newly dead mechanisms). Report a finding ONLY with
evidence. Skip speculative improvements and unrelated backlog. List deliberate non-findings —
things that look like friction but are working as designed.
{scope}

## Corpus (complete; nothing else is in scope)
{corpus}

End with one fence:
```{ANALYST_FENCE}
{{"findings": [{{"title": "...", "kind": "...", "fix": "...", "evidence": [{{"source": "corpus/NNN-name", "quote": "<verbatim>"}}]}}],
 "non_findings": [{{"title": "...", "why": "..."}}]}}
```"""
