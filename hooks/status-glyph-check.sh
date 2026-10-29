#!/usr/bin/env bash
# Stop hook: session-only schedules. If the session holds CronCreate /
# ScheduleWakeup jobs that are still pending, show Jake a non-blocking warning
# (hook `systemMessage`) that they die if the session closes. Those tools never
# write to disk, so closing the session silently cancels them.
#
# It used to also block turns whose last line lacked a sign-off glyph, asking
# for a one-line re-reply. That re-reply became the turn's final message, so
# focus view showed only the glyph and hid the real reply. The sign-off rule now
# lives in the instructions alone (output-styles/zen.md, the bootstrap CLAUDE.md,
# instructions/providers/claude-development.md); nothing blocks on it. The
# filename is kept so settings.json wiring doesn't churn.
#
# Never blocks; fails open (exit 0) on anything unexpected.
#
# Headless advance-roadmap runs (ADVANCE_ROADMAP_HEADLESS=1) get no warning:
# nobody is there to read it.
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

warning_json
exit 0
