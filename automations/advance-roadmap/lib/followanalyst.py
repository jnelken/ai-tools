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
ANALYST_TIMEOUT_S = float(os.environ.get("ADVANCE_ROADMAP_FOLLOWUP_ANALYST_TIMEOUT_S", "1800"))
TOKENS_TIMEOUT_S = 60
# Cap inlined corpus bytes so the prompt stays bounded (injection surface is the paste, not FS).
CORPUS_PROMPT_MAX_BYTES = int(os.environ.get("ADVANCE_ROADMAP_FOLLOWUP_ANALYST_CORPUS_BYTES", "200000"))
CODEX = os.environ.get("ADVANCE_ROADMAP_CODEX_BIN", "/opt/homebrew/bin/codex")
CLAUDE = os.environ.get("ADVANCE_ROADMAP_CLAUDE_BIN", "/Users/jake/.local/bin/claude")
PROVIDERS = ("codex", "claude")


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


def empty_analyst_cwd(st):
    """Empty stage-local cwd for provider invokes — no corpus files on disk for the model to read."""
    sandbox = followrepair.stage_dir(st["key"]) / "corpus-sandbox"
    if sandbox.exists():
        shutil.rmtree(sandbox)
    sandbox.mkdir(parents=True)
    return sandbox


def analyze(st, findings, files):
    """Optional read-only model pass. Returns (kept_findings, rejected_non_findings).

    Off / empty corpus → ([], []). Quota, timeout, crash, or missing fence leave deterministic
    findings untouched and record st['analyst'].
    """
    if not enabled() or not files:
        return [], []
    try:
        sandbox = empty_analyst_cwd(st)
    except (OSError, ValueError, TypeError) as e:
        # Optional analyst must never abort deterministic scan/recording.
        st["analyst"] = f"failed: sandbox prepare: {e}"
        return [], []
    files = [Path(f).resolve() for f in files]
    prompt = analyst_prompt(st, findings, files)
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
        # Echoed banners in a successful final answer are not a live limit (shared live-limit rule).
        banner = usagelib.live_limit_banner(
            transcript, exit_code=exit_code, final_answer=answer, provider_result=provider_result)
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
    try:
        return accept_findings(
            st, findings, files, doc if isinstance(doc, dict) else {}, provider=provider)
    finally:
        st.pop("_analyst_paste", None)


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
    kept, rejected = [], []
    for raw in raw_findings:
        if not isinstance(raw, dict):
            continue
        title = raw.get("title")
        if not isinstance(title, str) or not title.strip():
            continue
        kind = raw.get("kind") if isinstance(raw.get("kind"), str) and raw.get("kind") else "analyst"
        key = raw.get("key") if isinstance(raw.get("key"), str) and raw.get("key") else title
        fix = raw.get("fix") if isinstance(raw.get("fix"), str) else ""
        raw_ev = raw.get("evidence") or []
        # Every evidence entry must be a dict that verifies — drop the whole finding otherwise.
        if not isinstance(raw_ev, list) or not raw_ev or not all(isinstance(e, dict) for e in raw_ev):
            rejected.append(followscan.non_finding(
                title, "rejected: its evidence is not in the corpus",
                [e for e in raw_ev if isinstance(e, dict)] if isinstance(raw_ev, list) else []))
            continue
        if any(_resolve_source(e.get("source")) is None for e in raw_ev):
            rejected.append(followscan.non_finding(
                title, "rejected: its evidence is not in the corpus", raw_ev))
            continue
        if not all(followscan.evidence_holds(e, files, stamps) for e in raw_ev):
            rejected.append(followscan.non_finding(
                title, "rejected: its evidence is not in the corpus", raw_ev))
            continue
        # Also require the quote to appear in the exact pasted (possibly truncated) body.
        paste = st.get("_analyst_paste") or {}
        if paste:
            ok_paste = True
            for e in raw_ev:
                src = _resolve_source(e.get("source"))
                if src is None or not quote_in_paste(e.get("quote"), paste.get(src)):
                    ok_paste = False
                    break
            if not ok_paste:
                rejected.append(followscan.non_finding(
                    title, "rejected: its evidence is not in the corpus", raw_ev))
                continue
        try:
            ev_objs = [followscan.evidence(e["source"], e["quote"]) for e in raw_ev]
        except (OSError, TypeError, ValueError):
            # evidence_holds is whitespace-normalized; evidence() requires an exact span.
            rejected.append(followscan.non_finding(
                title, "rejected: its evidence is not in the corpus", raw_ev))
            continue
        f = followscan.finding(st["stage"], st["repo"], kind, title, key, ev_objs, fix)
        if f["id"] not in known:
            kept.append(f)
            known.add(f["id"])
    for raw in raw_non:
        if isinstance(raw, dict) and isinstance(raw.get("title"), str) and raw.get("title").strip():
            why = raw.get("why") if isinstance(raw.get("why"), str) else ""
            rejected.append(followscan.non_finding(raw["title"], why, []))
    return kept, rejected


def run_analyst(st, provider, prompt, sandbox):
    """(transcript, final answer, exit code, provider_result) of one read-only analyst call.

    Findings come only from the final answer (Codex's -o file, Claude's JSON result). Live limits
    use usage.live_limit_banner — exit status and provider error records, never a quoted banner alone.
    Process-group deadlines via procrun so a timeout never leaves descendants alive.

    Bounded-corpus policy (owner: analyze → inline_corpus + empty cwd): the prompt carries the
    corpus paste; the invoke cwd is an empty directory (no corpus files on disk for the model).
    Claude is granted no file-read tools. Absolute-path reads outside cwd are not OS-blocked.
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
        # read-only sandbox + empty writable roots; corpus is already in the prompt.
        p = procrun.run(
            [CODEX, "exec", "-o", str(final), "-s", "read-only", "-C", str(sandbox),
             "--skip-git-repo-check", prompt],
            timeout=ANALYST_TIMEOUT_S, text=True, cwd=str(sandbox))
        raw.write_text((p.stdout or "") + (p.stderr or ""))
        procrun.run(
            [*tokens, "codex", str(raw), "--role", "analyst",
             "--log", str(d / "model-usage.jsonl"), "--session-out", str(session)],
            timeout=TOKENS_TIMEOUT_S, text=True)
        transcript = raw.read_text(errors="replace")
    else:
        # No Read/Bash/Edit — answer only from the inlined corpus in the prompt.
        p = procrun.run(
            [CLAUDE, "-p", prompt, "--permission-mode", "plan",
             "--allowedTools", "",
             "--disallowedTools", "Read,Edit,Write,NotebookEdit,Bash,MultiEdit,Glob,Grep",
             "--output-format", "json"],
            timeout=ANALYST_TIMEOUT_S, text=True, cwd=str(sandbox))
        raw_json = d / "analyst-claude.json"
        raw_json.write_text((p.stdout or "") + (p.stderr or ""))
        procrun.run(
            [*tokens, "claude-json", str(raw_json), str(raw), "--final", str(final), "--role", "analyst",
             "--log", str(d / "model-usage.jsonl"), "--session-out", str(session)],
            timeout=TOKENS_TIMEOUT_S, text=True)
        transcript = followscan.read_text(raw) or raw_json.read_text(errors="replace")
        try:
            for line in raw_json.read_text(errors="replace").splitlines():
                if not line.startswith("{"):
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue  # skip malformed lines; keep scanning for a result object
                if isinstance(obj, dict) and "result" in obj:
                    provider_result = obj
                    break
        except OSError:
            provider_result = None
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


def _file_text_for_prompt(path, stamps):
    """File text for the prompt; runs.jsonl is stamp-filtered when stamps are known."""
    path = Path(path)
    if path.name == "runs.jsonl" and stamps:
        lines = []
        wanted = set(stamps)
        for line in followscan.iter_jsonl_lines(path):
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and row.get("stamp") in wanted:
                lines.append(line if line.endswith("\n") else line + "\n")
        return "".join(lines)
    return path.read_text(errors="replace")


def inline_corpus(files, max_bytes=CORPUS_PROMPT_MAX_BYTES, stamps=None):
    """Paste corpus file contents into the prompt. Returns (prompt_text, {source: pasted_body}).

    Owner of bounded-corpus network policy: providers receive only this paste — cwd has no corpus
    files. Budget is measured in UTF-8 bytes (not Python char length).
    """
    parts, used, pasted = [], 0, {}
    for path in files:
        path = Path(path).resolve()
        try:
            text = _file_text_for_prompt(path, stamps)
        except OSError as e:
            note = f"### {path}\n(unreadable: {e})\n"
            used += len(note.encode("utf-8"))
            parts.append(note)
            continue
        header = f"### {path}\n```\n"
        footer = "\n```\n"
        overhead = len(header.encode("utf-8")) + len(footer.encode("utf-8"))
        room = max_bytes - used - overhead
        if room <= 0:
            parts.append(f"### {path}\n(truncated: corpus budget exhausted)\n")
            break
        marker = "\n…(truncated)"
        marker_n = len(marker.encode("utf-8"))
        if len(text.encode("utf-8")) <= room:
            body = text
        elif room < marker_n:
            body = _utf8_budget_slice(text, room)
        else:
            body = _utf8_budget_slice(text, room - marker_n) + marker
        pasted[str(path)] = body
        parts.append(f"{header}{body}{footer}")
        used += len(parts[-1].encode("utf-8"))
    return "\n".join(parts), pasted


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
    corpus, pasted = inline_corpus(files, stamps=corpus_stamps(st))
    st["_analyst_paste"] = pasted
    return f"""You are the read-only analyst for advance-roadmap's {stage} follow-up ({st.get('key')}).
Do not open, search, or read any files on disk. The entire allowed corpus is pasted below.
Cite evidence only with a source path from that paste and a verbatim quote from it.
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
{{"findings": [{{"title": "...", "kind": "...", "fix": "...", "evidence": [{{"source": "<path>", "quote": "<verbatim>"}}]}}],
 "non_findings": [{{"title": "...", "why": "..."}}]}}
```"""
