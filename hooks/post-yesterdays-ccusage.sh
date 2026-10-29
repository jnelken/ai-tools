#!/usr/bin/env bash
# Post ccusage daily summaries to Slack (Claude + Codex usage in one
# message), catching up any missed days since the last successful post.
# Triggered by SessionStart in ~/.claude/settings.json.
#
# Requires:
#   - $SLACK_CCUSAGE_WEBHOOK_URL set (incoming webhook for a Slack channel)
#   - `npx`, `jq`, `curl` on PATH
#   - ccusage >= 20 for Codex data (older versions still post Claude-only)
#
# Table: one table for both providers, every model sorted by output tokens (most
# first), columns Model Cost Input Output, then one column per reasoning-effort
# level used that day (Min Low Med High XHigh Max), each row's cost split by
# effort as a percentage. Then one total row per provider (Claude, Codex). There
# are no cache columns: the Claude cache is the line "_Cache: 7.2M written ·
# 209.7M read_" under the header, which sparky reads for its cache rankings.
# ccusage has no effort field, so lib/effort-mix.py reads it from Claude Code's
# transcripts and Codex's rollouts; with no data the effort columns are left out.
# sparky must parse the extra columns first (ccusageLeaderboard.ts,
# EFFORT_COLUMNS), or a post that carries them is dropped from the leaderboard.
#
# State: ~/.claude/.ccusage-state/last-posted holds the most recent
# YYYY-MM-DD that was successfully posted. Catchup posts every day from
# last+1 through yesterday, one message per day. The marker advances only
# after each successful POST, so transient Slack failures resume cleanly.
# The already-posted check runs first and costs one `date` on macOS, before
# any state-directory, lock, or git work (GNU date uses a fallback).
#
# Failures: a failed ccusage run or Slack POST is appended to
# ~/.claude/.ccusage-state/errors.log and raises one macOS notification per
# day (nothing goes to Slack — the webhook channel feeds sparky's leaderboard).
# A half-installed ccusage in the npx cache ("native binary is not available")
# is evicted and retried once; npx reuses an entry that already matches
# `@latest`, so without that it fails silently on every session.

set -u  # NOT -e — graceful no-op on missing tools / transient failures

WEBHOOK="${SLACK_CCUSAGE_WEBHOOK_URL:-}"
[ -z "$WEBHOOK" ] && { echo "[ccusage-hook] SLACK_CCUSAGE_WEBHOOK_URL unset; skipping" >&2; exit 0; }

for bin in npx jq curl; do
  command -v "$bin" >/dev/null 2>&1 || { echo "[ccusage-hook] $bin not on PATH; skipping" >&2; exit 0; }
done

state_dir="$HOME/.claude/.ccusage-state"
last_file="$state_dir/last-posted"

# Yesterday in YYYY-MM-DD (BSD `date` on macOS; GNU `date` elsewhere)
yesterday=$(date -v-1d +%Y-%m-%d 2>/dev/null)
[ -n "$yesterday" ] || yesterday=$(date -d 'yesterday' +%Y-%m-%d)

last_posted=""
if [ -r "$last_file" ]; then
  read -r last_posted 2>/dev/null < "$last_file" || true
fi
date_pattern='^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
if [[ "$last_posted" =~ $date_pattern ]] \
   && [[ "$last_posted" > "$yesterday" || "$last_posted" == "$yesterday" ]]; then
  exit 0
fi

mkdir -p "$state_dir" 2>/dev/null || exit 0

# Who's posting — prefer git global identity, fall back to $USER@hostname
git_name=$(git config --global user.name 2>/dev/null || true)
git_email=$(git config --global user.email 2>/dev/null || true)
if [ -n "$git_name" ] && [ -n "$git_email" ]; then
  who="$git_name <$git_email>"
elif [ -n "$git_email" ]; then
  who="$git_email"
elif [ -n "$git_name" ]; then
  who="$git_name"
else
  who="${USER:-unknown}@$(hostname -s 2>/dev/null || echo unknown)"
fi

# Atomic claim — keyed on yesterday so parallel sessions don't both catch up
lock="$state_dir/.lock-$yesterday"
# If the lock is stale (left by a SIGKILL'd async hook), remove it first.
if [ -d "$lock" ]; then
  lock_age=$(( $(date +%s) - $(stat -f %m "$lock" 2>/dev/null || stat -c %Y "$lock" 2>/dev/null || echo 0) ))
  [ "$lock_age" -gt 7200 ] && rmdir "$lock" 2>/dev/null || true
fi
mkdir "$lock" 2>/dev/null || exit 0
err_file=$(mktemp "${TMPDIR:-/tmp}/ccusage-hook.XXXXXX" 2>/dev/null) || err_file=/dev/null
trap 'rmdir "$lock" 2>/dev/null || true; [ "$err_file" = /dev/null ] || rm -f "$err_file"' EXIT

# Evict npx cache entries for ccusage@latest. The entry hash is derived from the
# package spec, so this removes exactly the entry the hook's npx calls reuse.
evict_ccusage_npx_cache() {
  local cache entry
  cache=$(npm config get cache 2>/dev/null) || cache=""
  [ -n "$cache" ] || cache="$HOME/.npm"
  for entry in "$cache"/_npx/*/; do
    grep -q '"ccusage@latest"' "${entry}package.json" 2>/dev/null && rm -rf "$entry"
  done
  return 0
}

# `npx -y ccusage@latest <args>` — stdout is the result, stderr lands in
# $err_file for the failure report. Retries once after evicting the cache when
# the native binary is missing (a half-installed cache entry).
run_ccusage() {
  local out rc
  out=$(npx -y ccusage@latest "$@" 2>"$err_file"); rc=$?
  if [ "$rc" -ne 0 ] && grep -q 'native binary is not available' "$err_file" 2>/dev/null; then
    echo "[ccusage-hook] ccusage native binary missing; evicting npx cache and retrying" >&2
    evict_ccusage_npx_cache
    out=$(npx -y ccusage@latest "$@" 2>"$err_file"); rc=$?
  fi
  printf '%s' "$out"
  return "$rc"
}

# effort_mix <ccusage json> <day> [--codex] — prints the effort-mix JSON for the
# day, or {} if it cannot be computed. Tries python3 on PATH first, then the
# system one: a version-manager shim (mise, pyenv) can exit non-zero depending on
# the directory or HOME it is run from, and the post must not depend on that.
effort_mix() {
  local py out helper
  helper="$(dirname "$0")/lib/effort-mix.py"
  for py in python3 /usr/bin/python3; do
    command -v "$py" >/dev/null 2>&1 || continue
    out=$(printf '%s' "$1" | "$py" "$helper" ${3:-} "$2" 2>/dev/null) && [ -n "$out" ] && { printf '%s' "$out"; return 0; }
  done
  printf '{}'
}

# report_failure <day> <what> — log it, and notify at most once per calendar day
# so a persistent failure doesn't re-alert on every session start.
report_failure() {
  local msg="ccusage hook: $2 for $1" today
  echo "[ccusage-hook] $msg" >&2
  {
    printf '%s %s\n' "$(date '+%F %T')" "$msg"
    head -c 400 "$err_file" 2>/dev/null | sed 's/^/    /'
    echo
  } >> "$state_dir/errors.log" 2>/dev/null
  tail -n 200 "$state_dir/errors.log" > "$state_dir/errors.log.tmp" 2>/dev/null \
    && mv "$state_dir/errors.log.tmp" "$state_dir/errors.log" 2>/dev/null
  today=$(date +%F)
  if [ "$(cat "$state_dir/last-notified" 2>/dev/null)" != "$today" ]; then
    printf '%s' "$today" > "$state_dir/last-notified"
    command -v osascript >/dev/null 2>&1 \
      && osascript -e "display notification \"$msg\" with title \"ccusage Slack post missed\"" >/dev/null 2>&1
  fi
  return 0
}

# Start = day after last-posted, or yesterday if first run
if last_posted=$(cat "$last_file" 2>/dev/null) && [ -n "$last_posted" ]; then
  start=$(date -j -v+1d -f "%Y-%m-%d" "$last_posted" +%Y-%m-%d 2>/dev/null \
       || date -d "$last_posted + 1 day" +%Y-%m-%d)
else
  start="$yesterday"
fi

# Nothing to do if already caught up
[[ "$start" > "$yesterday" ]] && exit 0

current="$start"
while [[ ! "$current" > "$yesterday" ]]; do
  # ccusage demands YYYYMMDD (no dashes)
  compact="${current//-/}"
  # Pretty-printed date for display, e.g. "Wed, May 14". internal-tools'
  # sparky parses this header back out of Slack (weekday
  # prefix optional there, for backward compat with already-posted messages
  # in this old no-weekday shape) — see resolveUsageDate in
  # services/sparky/src/ccusageLeaderboard.ts.
  pretty=$(date -j -f "%Y-%m-%d" "$current" "+%a, %b %-d" 2>/dev/null \
        || date -d "$current" "+%a, %b %-d" 2>/dev/null \
        || printf '%s' "$current")
  # `ccusage claude daily` (not the top-level `ccusage daily`) is what still
  # emits `modelBreakdowns[]`. The top-level command went multi-agent and only
  # returns `modelsUsed` (a flat array of names), which collapses our table to
  # just the Total row. We make a second call for `codex daily` and merge in jq.
  claude_json=$(run_ccusage claude daily --since "$compact" --until "$compact" --json) || {
    report_failure "$current" "ccusage claude failed"
    break
  }
  # Codex support landed in ccusage 20. On older versions the subcommand exits
  # with "Command not found: codex" — treat that (and any other failure) as an
  # empty result so we degrade to a Claude-only post instead of breaking.
  codex_json=$(run_ccusage codex daily --since "$compact" --until "$compact" --json) || codex_json='{}'
  [ -z "$codex_json" ] && codex_json='{}'

  # Reasoning-effort mix per model row, appended after Output (ccusage has no
  # effort field; Claude Code's transcripts do). Any failure or no data yields
  # {} and the table is exactly what it was before.
  effort_json=$(effort_mix "$claude_json" "$current")
  codex_effort_json=$(effort_mix "$codex_json" "$current" --codex)

  summary=$(jq -nr \
    --argjson c "$claude_json" \
    --argjson x "$codex_json" \
    --argjson e "$effort_json" \
    --argjson xe "$codex_effort_json" \
    --arg d "$pretty" --arg who "$who" '
    def fmt:
      if . >= 1000000 then
        (. * 10 / 1000000 | floor) as $t |
        ($t / 10 | floor) as $w | ($t - $w * 10) as $r |
        "\($w).\($r)M"
      elif . >= 1000 then
        (. * 10 / 1000 | floor) as $t |
        ($t / 10 | floor) as $w | ($t - $w * 10) as $r |
        "\($w).\($r)K"
      else tostring end;
    def money:
      (. * 100 | floor) as $c |
      "$\($c/100 | floor)." + (
        ($c % 100) as $r |
        if $r < 10 then "0\($r)" else "\($r)" end
      );
    def cap: "\(.[0:1] | ascii_upcase)\(.[1:])";
    def claudeModel:
      (capture("claude-(?<fam>opus|sonnet|haiku|fable)-(?<v>\\d+-\\d+)") // null) as $m |
      if $m then "\($m.fam | cap) \($m.v | gsub("-"; "."))" else . end;
    def lpad($n):
      tostring as $s | ($n - ($s | length)) as $p |
      (if $p > 0 then (" " * $p) else "" end) + $s;
    def rpad($n):
      tostring as $s | ($n - ($s | length)) as $p |
      $s + (if $p > 0 then (" " * $p) else "" end);
    # One row layout for every provider: model(12, left, cut to fit)  cost  in  out,
    # each 7 wide and right-aligned, then effort cells. A cell with no value (Codex
    # cost per model) is blank, which keeps the columns aligned. Cache is not a
    # column: the Claude cache goes on its own line under the header.
    # eff: effort-mix cells appended after Output, each two spaces plus 5 wide
    # (header labels, or "NN%"); empty for a row with no effort data. No
    # apostrophes in comments: this whole program sits inside shell single quotes.
    def rowAll(model; cost; tin; tout; eff):
      (model | tostring | .[0:12] | rpad(12)) + "  " +
      (cost  | lpad(7))  + "  " +
      (tin   | lpad(7))  + "  " +
      (tout  | lpad(7))  + eff;
    # gpt-6-luna -> "Luna 6", gpt-5.3-codex -> "Codex 5.3", gpt-5.5 -> "GPT 5.5"
    def codexModel:
      (capture("^gpt-(?<v>[0-9][0-9.]*)(?:-(?<n>.+))?$") // null) as $m |
      if $m then ((if $m.n == null then "GPT" else ($m.n | split("-") | map(cap) | join(" ")) end) + " " + $m.v) else . end;
    def lvOrder: ["Min","Low","Med","High","XHigh","Max"];
    # Re-lay a row percentages (laid out over its own level list) onto the shared
    # columns; a level the row never used is 0%.
    def remap($own; $lv):
      if . == null or ($own | length) == 0 then null
      else . as $p | [ $lv[] as $l | ($own | index($l)) as $i | if $i == null then 0 else $p[$i] end ]
      end;
    def effCells: if . == null then "" else map("  " + (tostring | lpad(5))) | join("") end;
    def pctCells: if . == null then "" else map("\(.)%") | effCells end;
    def sepAll($n): ("─" * (39 + 7 * $n));

    # Pull the single-day row from each report. ccusage emits {"daily": [...]}
    # for days with data, bare [] for days with none, and we coerce a parse
    # failure to {} so the "no activity" branch fires cleanly.
    ((if ($c | type) == "object" then ($c.daily // []) else ($c // []) end) | (.[0] // {})) as $cr |
    ((if ($x | type) == "object" then ($x.daily // []) else ($x // []) end) | (.[0] // {})) as $xr |

    ($cr != {}) as $chas |
    ($xr != {}) as $xhas |
    ($cr.totalCost // 0) as $ccost |
    ($xr.costUSD   // 0) as $xcost |

    # Effort columns shared by both providers: the levels either one used that day.
    (($e.levels // []) + ($xe.levels // [])) | unique as $used |
    ([lvOrder[] | select(. as $l | $used | index($l))] + [$used[] | select(. as $l | lvOrder | index($l) | not)]) as $lv |

    # One table of every model from both providers, most output tokens first.
    ((($cr.modelBreakdowns // []) | map({
        name: (.modelName | claudeModel), cost: (.cost // 0 | money),
        tin: (.inputTokens // 0), tout: (.outputTokens // 0),
        pct: ($e.rows[.modelName] | remap($e.levels // []; $lv)) }))
      +
      (($xr.models // {}) | to_entries | map({
        name: (.key | codexModel), cost: "",
        tin: (.value.inputTokens // 0), tout: (.value.outputTokens // 0),
        pct: ($xe.rows[.key] | remap($xe.levels // []; $lv)) }))
    | sort_by(-.tout)) as $rows |

    if ($chas | not) and ($xhas | not) then
      "_\($d) — \($who): no activity_"
    else
      # Header: grand total, sub-totals beneath
      "*\($d) — \($who): \(($ccost + $xcost) | money)*\n" +
      "_Claude " + (if $chas then ($ccost | money) else "none" end) +
      " · Codex "  + (if $xhas then ($xcost | money) else "none" end) + "_\n" +
      (if $chas then "_Cache: \($cr.cacheCreationTokens // 0 | fmt) written · \($cr.cacheReadTokens // 0 | fmt) read_\n" else "" end) +
      "```\n" +
      rowAll("Model"; "Cost"; "Input"; "Output"; (if ($lv | length) > 0 then ($lv | effCells) else "" end)) + "\n" +
      (if ($rows | length) > 0
        then ($rows | map(rowAll(.name; .cost; .tin | fmt; .tout | fmt; .pct | pctCells)) | join("\n"))
             + "\n" + sepAll($lv | length) + "\n"
        else "" end) +
      # One total row per provider that had activity.
      ([ (if $chas then
            rowAll("Claude"; $ccost | money; $cr.inputTokens // 0 | fmt; $cr.outputTokens // 0 | fmt;
                   ($e.total | remap($e.levels // []; $lv) | pctCells))
          else empty end),
         (if $xhas then
            rowAll("Codex"; $xcost | money; $xr.inputTokens // 0 | fmt; $xr.outputTokens // 0 | fmt;
                   ($xe.total | remap($xe.levels // []; $lv) | pctCells))
          else empty end) ] | join("\n")) +
      "\n```"
    end
  ' 2>/dev/null) || summary="$pretty — $who: (parse error)"

  # No Claude or Codex usage that day — skip the Slack post entirely (still
  # advance the marker so catchup doesn't re-check this day every session).
  if [[ "$summary" == "_"*" no activity_" ]]; then
    printf '%s' "$current" > "$last_file"
    current=$(date -j -v+1d -f "%Y-%m-%d" "$current" +%Y-%m-%d 2>/dev/null \
           || date -d "$current + 1 day" +%Y-%m-%d)
    continue
  fi

  payload=$(jq -n --arg t "$summary" '{text: $t}')
  if curl -sf -X POST -H 'Content-Type: application/json' -d "$payload" "$WEBHOOK" > /dev/null; then
    printf '%s' "$current" > "$last_file"   # advance ONLY on success
  else
    : > "$err_file"
    report_failure "$current" "slack POST failed (will retry next session)"
    break
  fi

  current=$(date -j -v+1d -f "%Y-%m-%d" "$current" +%Y-%m-%d 2>/dev/null \
         || date -d "$current + 1 day" +%Y-%m-%d)
  sleep 1
done
