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


def materialize_corpus_sandbox(st, files):
    """Copy only the corpus into a stage-local sandbox. Returns (sandbox_dir, sandbox→original map).

    Invariant owner for bounded-corpus FS access: run_analyst always uses this sandbox as cwd / -C.
    """
    sandbox = followrepair.stage_dir(st["key"]) / "corpus-sandbox"
    if sandbox.exists():
        shutil.rmtree(sandbox)
    sandbox.mkdir(parents=True)
    mapping = {}
    for i, src in enumerate(files):
        src = Path(src).resolve()
        dest = (sandbox / f"{i:03d}-{src.name}").resolve()
        shutil.copy2(src, dest)
        mapping[str(dest)] = src
    return sandbox, mapping


def to_sandbox_evidence(raw_ev, mapping):
    """Map evidence sources onto sandbox copies (accepts sandbox or original paths)."""
    reverse = {str(orig.resolve()): sand for sand, orig in mapping.items()}
    out = []
    for e in raw_ev:
        try:
            src = str(Path(e.get("source") or "").resolve())
        except OSError:
            return None
        if src in mapping:
            out.append({**e, "source": src})
        elif src in reverse:
            out.append({**e, "source": reverse[src]})
        else:
            return None
    return out


def to_original_evidence(raw_ev, mapping):
    """Rewrite sandbox evidence sources to the original corpus paths."""
    out = []
    for e in raw_ev:
        src = str(Path(e.get("source") or "").resolve())
        original = mapping.get(src)
        if original is None:
            return None
        out.append({**e, "source": str(original)})
    return out


def analyze(st, findings, files):
    """Optional read-only model pass. Returns (kept_findings, rejected_non_findings).

    Off / empty corpus → ([], []). Quota, timeout, crash, or missing fence leave deterministic
    findings untouched and record st['analyst'].
    """
    if not enabled() or not files:
        return [], []
    sandbox, mapping = materialize_corpus_sandbox(st, files)
    sandbox_files = [Path(p) for p in mapping]
    prompt = analyst_prompt(st, findings, sandbox_files)
    # Invariant (owner: analyze gate check): every path to run_analyst must see an
    # available gate; an unreadable gate (None) is unavailable — fail closed.
    limited, errored = [], []
    provider = None
    answer = ""
    for provider in PROVIDERS:
        gate = followrepair.gate_one("analyst", provider)
        st.setdefault("usage_snapshots", []).append({
            "at": followrepair.now_iso(), "role": "analyst", "provider": provider, "gate": gate})
        if gate is None or not gate.get("available"):
            continue
        try:
            transcript, answer, exit_code, provider_result = run_analyst(
                st, provider, prompt, sandbox)
        except (OSError, subprocess.TimeoutExpired) as e:
            st["analyst"] = f"failed: {e}"
            return [], []
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
        if limited:
            st["analyst"] = (
                f"skipped: {' and '.join(limited)} hit "
                f"{'their limits' if len(limited) > 1 else 'its limit'}")
        elif errored:
            st["analyst"] = f"failed: {' and '.join(errored)} exited with error"
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
        st, findings, sandbox_files, doc if isinstance(doc, dict) else {}, mapping=mapping)


def accept_findings(st, findings, files, doc, mapping=None):
    """Keep analyst findings whose every quote verifies in the corpus; reject the rest.

    When mapping is set (sandbox → original), evidence sources are remapped to originals after
    verification against the sandbox copies.
    """
    known = {f["id"] for f in findings}
    stamps = corpus_stamps(st)
    kept, rejected = [], []
    for raw in doc.get("findings") or []:
        if not isinstance(raw, dict) or not raw.get("title"):
            continue
        raw_ev = raw.get("evidence") or []
        # Every evidence entry must be a dict that verifies — drop the whole finding otherwise.
        if not isinstance(raw_ev, list) or not raw_ev or not all(isinstance(e, dict) for e in raw_ev):
            rejected.append(followscan.non_finding(
                raw["title"], "rejected: its evidence is not in the corpus",
                [e for e in raw_ev if isinstance(e, dict)] if isinstance(raw_ev, list) else []))
            continue
        check_ev = raw_ev
        if mapping is not None:
            check_ev = to_sandbox_evidence(raw_ev, mapping)
            if check_ev is None:
                rejected.append(followscan.non_finding(
                    raw["title"], "rejected: its evidence is not in the corpus", raw_ev))
                continue
        if not all(followscan.evidence_holds(e, files, stamps) for e in check_ev):
            rejected.append(followscan.non_finding(
                raw["title"], "rejected: its evidence is not in the corpus", check_ev))
            continue
        if mapping is not None:
            remapped = to_original_evidence(check_ev, mapping)
            if remapped is None:
                rejected.append(followscan.non_finding(
                    raw["title"], "rejected: its evidence is not in the corpus", check_ev))
                continue
            raw_ev = remapped
        else:
            raw_ev = check_ev
        f = followscan.finding(
            st["stage"], st["repo"], str(raw.get("kind") or "analyst"), raw["title"],
            raw.get("key") or raw["title"],
            [followscan.evidence(e["source"], e["quote"]) for e in raw_ev],
            raw.get("fix") or "")
        if f["id"] not in known:
            kept.append(f)
            known.add(f["id"])
    for raw in doc.get("non_findings") or []:
        if isinstance(raw, dict) and raw.get("title"):
            rejected.append(followscan.non_finding(raw["title"], raw.get("why") or "", []))
    return kept, rejected


def run_analyst(st, provider, prompt, sandbox):
    """(transcript, final answer, exit code, provider_result) of one read-only analyst call.

    Findings come only from the final answer (Codex's -o file, Claude's JSON result). Live limits
    use usage.live_limit_banner — exit status and provider error records, never a quoted banner alone.
    Process-group deadlines via procrun so a timeout never leaves descendants alive.

    Invariant (owner: run_analyst cwd/-C): both provider invokes use the corpus sandbox only —
    never the repo root — so filesystem access is limited to the selected corpus copies.
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
        p = procrun.run(
            [CLAUDE, "-p", prompt, "--permission-mode", "plan",
             "--allowedTools", "Read",
             "--disallowedTools", "Edit,Write,NotebookEdit,Bash,MultiEdit",
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
                if line.startswith("{"):
                    obj = json.loads(line)
                    if isinstance(obj, dict) and "result" in obj:
                        provider_result = obj
                        break
        except (OSError, ValueError):
            provider_result = None
    sid = (followscan.read_text(session) or "").strip()
    if sid:
        st.setdefault("sessions", []).append(
            {"role": "analyst", "provider": provider, "session_id": sid})
    return transcript, followscan.read_text(final) or "", p.returncode, provider_result


def record_analyst_limit(st, provider, text):
    """Keep the hit in the stage first, then gate the provider for everyone else."""
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
    try:
        followrepair.usage(
            "record-limit", "--provider", provider, "--text", text, "--scope", "analyst")
    except (OSError, subprocess.TimeoutExpired):
        pass


def analyst_prompt(st, findings, files):
    stage = st.get("stage") or ""
    cleanup = stage == "project-cleanup"
    scope = (f"Only project-specific bugs in {st.get('repo')} count here."
             if cleanup else
             "Only orchestration, routing, skills, hooks and reporting count here.")
    return f"""You are the read-only analyst for advance-roadmap's {stage} follow-up ({st.get('key')}).
Read only these files: {', '.join(str(f) for f in files)}
These findings are already recorded (do not repeat them): {json.dumps([f['title'] for f in findings])}

Apply session-hardening's recognition tests (wrong knowledge, misfiring automation, repeated manual
work, fragile tooling, misleading surfaces, newly dead mechanisms). Report a finding ONLY with
evidence: for each, the exact file path from the list above and a verbatim quote of at least one
line from it. Skip speculative improvements and unrelated backlog. List deliberate non-findings —
things that look like friction but are working as designed.
{scope}

End with one fence:
```{ANALYST_FENCE}
{{"findings": [{{"title": "...", "kind": "...", "fix": "...", "evidence": [{{"source": "<path>", "quote": "<verbatim>"}}]}}],
 "non_findings": [{{"title": "...", "why": "..."}}]}}
```"""
