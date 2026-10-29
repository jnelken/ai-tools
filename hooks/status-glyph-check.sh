#!/usr/bin/env bash
# Stop hook, two jobs:
#
# 1. Sign-off: every final assistant message must end with a status line that
#    starts with one of the sign-off glyphs defined in the global CLAUDE.md
#    ("Response Sign-off Glyphs"). If it doesn't, block once so Claude appends
#    one.
# 2. Session-only schedules: if the session holds CronCreate / ScheduleWakeup
#    jobs that are still pending, show Jake a non-blocking warning (hook
#    `systemMessage`) that they die if the session closes. Those tools never
#    write to disk, so closing the session silently cancels them.
#
# Fails open (exit 0) on anything unexpected — this must never wedge a
# session.
#
# ⚠️ and ⛏️ count only in their emoji form (followed by U+FE0F); the bare
# text-presentation glyphs render monochrome and are rejected on purpose.
#
# Headless advance-roadmap runs (ADVANCE_ROADMAP_HEADLESS=1, exported by run.sh, worker.sh,
# review.sh and planapproval.py) are exempt: a forced glyph turn replaces the planner's final
# answer, which is where its ORCHESTRATOR_RESULT_JSON fence lives, and no one reads the sign-off.
# CLAUDE_QUOTA_MANAGED can't stand in for this: planapproval.py calls claude directly, and any
# interactive claude started inside a managed session would inherit it.
#
# Test seam: STATUS_GLYPH_NOW_EPOCH overrides "now" (seconds) for the schedule
# check.

set -u  # NOT -e — graceful no-op on any failure

command -v jq >/dev/null 2>&1 || exit 0

input=$(cat 2>/dev/null || true)
[ -z "$input" ] && exit 0
[ "${ADVANCE_ROADMAP_HEADLESS:-}" = "1" ] && exit 0

transcript=$(printf '%s' "$input" | jq -r '.transcript_path // empty' 2>/dev/null || true)

# --- epoch helpers (BSD date on macOS, GNU date elsewhere) -------------------
to_epoch() { # to_epoch "YYYY-MM-DD HH:MM" -> epoch seconds, empty if invalid
  date -j -f "%Y-%m-%d %H:%M" "$1" +%s 2>/dev/null || date -d "$1" +%s 2>/dev/null || true
}
fmt_epoch() { # fmt_epoch <epoch> -> "Fri Oct 16 12:02PM EDT"
  date -r "$1" +"%a %b %-d %-I:%M%p %Z" 2>/dev/null || date -d "@$1" +"%a %b %-d %-I:%M%p %Z" 2>/dev/null || echo "$1"
}

# --- pending session-only schedules -------------------------------------------
# Prints one human-readable item per line for each job that would be lost if
# the session closed. Prints nothing when there are none or on any problem.
pending_schedules() {
  [ -n "$transcript" ] && [ -f "$transcript" ] || return 0
  local now rows kind a b c d
  now=${STATUS_GLYPH_NOW_EPOCH:-$(date +%s)}

  # Prefilter with grep so a huge transcript is never slurped whole; jq only
  # sees the handful of lines that can matter. Subagent (sidechain) entries
  # are ignored: their schedules belong to them, not to this session.
  rows=$(grep -E 'CronCreate|CronDelete|ScheduleWakeup|Scheduled (one-shot|recurring)' "$transcript" 2>/dev/null | jq -rs '
    def epoch: sub("\\.[0-9]+Z$"; "Z") | fromdateiso8601;
    [ .[] | select((.isSidechain // false) | not) ] as $e
    | [ $e[] | select(.type == "assistant") | .timestamp as $ts
        | .message.content[]? | select(.type == "tool_use") | . + {ts: $ts} ] as $uses
    | ( [ $uses[] | select(.name == "CronCreate") | .id ] ) as $createIds
    | ( [ $e[] | select(.type == "user") | .message.content
          | if type == "array" then .[] else empty end
          | select(.type == "tool_result" and (.tool_use_id as $i | $createIds | index($i)))
          | { key: .tool_use_id,
              value: ((.content | tostring)
                      | capture("Scheduled [a-z -]+ (?<id>[0-9a-zA-Z]{6,}) \\(").id? // "") } ]
        | from_entries ) as $jobIds
    | ( [ $uses[] | select(.name == "CronDelete") | .input | [ .. | strings ] | .[] ] ) as $deleted
    | ( [ $uses[] | select(.name == "ScheduleWakeup") ] | last ) as $wake
    | ( $uses[] | select(.name == "CronCreate")
        | ($jobIds[.id] // "") as $jid
        | select(($deleted | index($jid)) | not)
        | [ "cron", (if $jid == "" then "-" else $jid end), (.input.cron // ""), (if .input.recurring == false then "false" else "true" end), (.ts | epoch | tostring) ]
        | @tsv ),
      ( $wake | select(. != null and ((.input.stop // false) | not))
        | [ "wake", "-", ((.input.delaySeconds // 0) | tostring), (.input.reason // ""), (.ts | epoch | tostring) ]
        | @tsv )
  ' 2>/dev/null || true)
  [ -n "$rows" ] || return 0

  while IFS=$'\t' read -r kind a b c d; do
    [ -n "$kind" ] || continue
    if [ "$kind" = "wake" ]; then
      # a=(unused) b=delaySeconds c=reason d=created epoch
      local fire=$(( d + b ))
      if [ "$now" -lt $(( fire + 60 )) ]; then
        echo "ScheduleWakeup (fires ~$(fmt_epoch "$fire"))"
      fi
      continue
    fi
    # kind=cron: a=job id b=cron c=recurring d=created epoch
    local label=$a; [ "$label" = "-" ] && label=job
    if [ "$c" = "false" ]; then
      read -r m h dom mon dow <<<"$b"
      if [[ "$m" =~ ^[0-9]+$ && "$h" =~ ^[0-9]+$ && "$dom" =~ ^[0-9]+$ && "$mon" =~ ^[0-9]+$ ]]; then
        local y fire="" cand
        y=$(date -r "$d" +%Y 2>/dev/null || date -d "@$d" +%Y 2>/dev/null || true)
        for yy in "$y" "$(( y + 1 ))"; do
          cand=$(to_epoch "$(printf '%04d-%02d-%02d %02d:%02d' "$yy" "$mon" "$dom" "$h" "$m")")
          if [ -n "$cand" ] && [ "$cand" -ge $(( d - 60 )) ]; then fire=$cand; break; fi
        done
        if [ -n "$fire" ]; then
          # 2 min grace so a job firing right now still counts as pending.
          [ "$now" -lt $(( fire + 120 )) ] && echo "CronCreate $label (one-shot, fires $(fmt_epoch "$fire"))"
          continue
        fi
      fi
      # Unparseable one-shot: assume pending for a day rather than hide it.
      [ "$now" -lt $(( d + 86400 )) ] && echo "CronCreate $label (one-shot, '$b')"
    else
      # Recurring jobs auto-expire after 7 days (their last run is the final one).
      local exp=$(( d + 7 * 86400 ))
      [ "$now" -lt "$exp" ] && echo "CronCreate $label (recurring '$b', expires $(fmt_epoch "$exp"))"
    fi
  done <<<"$rows"
}

warning_json() { # warning_json -> JSON with systemMessage, or nothing
  local items msg
  items=$(pending_schedules 2>/dev/null || true)
  [ -n "$items" ] || return 0
  msg=$(printf '%s' "$items" | paste -sd ';' - | sed 's/;/; /g')
  jq -n --arg m "⏰ Session-only schedule — lost if this session closes: $msg" '{systemMessage: $m}'
}

# Already sent back once this turn — never loop. Still surface schedules.
active=$(printf '%s' "$input" | jq -r '.stop_hook_active // false' 2>/dev/null || echo false)
if [ "$active" = "true" ]; then
  warning_json
  exit 0
fi

# Prefer the message the harness hands us; fall back to the transcript. In
# the transcript each content block can be its own entry, so only the LAST
# assistant entry counts: if it's a tool_use (AskUserQuestion, ExitPlanMode)
# there is no final text to check.
text=$(printf '%s' "$input" | jq -r '.last_assistant_message // empty | if type == "string" then . else empty end' 2>/dev/null || true)
if [ -z "$text" ]; then
  [ -n "$transcript" ] && [ -f "$transcript" ] || exit 0
  text=$(jq -rs '
    [ .[] | select(.type == "assistant" and (.isSidechain | not)) ] | last
    | (.message.content // [])
    | if type == "array" then [ .[] | select(.type == "text") | .text ] | join("\n") else . end
  ' "$transcript" 2>/dev/null || true)
fi
[ -z "$text" ] && exit 0

ok=$(printf '%s' "$text" | jq -Rrs '
  [ split("\n")[] | sub("\\s+$"; "") | select(length > 0) ] | last // ""
  | sub("^[—–-]\\s*"; "")
  | test("^(🛑|⚠️|⏳|💡|✅|⛏️|💬)")
' 2>/dev/null || echo true)
if [ "$ok" = "true" ]; then
  warning_json
  exit 0
fi

jq -n '{
  decision: "block",
  reason: "Your final message is missing its sign-off line. Reply with ONLY a single line: <glyph> <label, 8 words max>. Glyphs (priority order): 🛑 blocked on Jake · ⚠️ stopped short/caveat · ⏳ idle, watching, resumes on its own · 💡 done, follow-ups worth a look · ⛏️ done + sswt reapable · ✅ done · 💬 answer only. Write ⚠️ and ⛏️ in emoji form (with U+FE0F)."
}'
exit 0
