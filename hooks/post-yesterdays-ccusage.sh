#!/usr/bin/env bash
# Post ccusage daily summaries to Slack (Claude + Codex usage in one
# message), catching up any missed days since the last successful post.
# Triggered by SessionStart in ~/.claude/settings.json.
#
# One machine posts for all of them. Only the host whose `hostname -s` is
# $CCUSAGE_POSTER (default jake1) posts; on any other machine the SessionStart run
# is a no-op. Before posting a day, the poster collects the same day from every
# Superset host in $CCUSAGE_PEERS (default jake2) and posts their sum as one
# message. It reaches a peer through a Superset terminal (no SSH between the
# machines): `superset terminals create --command` runs this script with
# `--collect` there, which prints the day's data gzipped, base64'd and framed with
# a sha256, and the poster reads it back off the terminal screen. A peer that is
# offline or fails holds the day (the marker does not advance), so the next
# session retries and the catch-up loop posts it once the peer answers.
# CCUSAGE_POSTER= (empty) restores one post per machine; CCUSAGE_PEERS= posts
# this machine alone.
#
# Modes:
#   (no args)              SessionStart: post missed days (poster only)
#   --collect START [END]  print this machine's data for those days, framed (peers)
#   --dry-run [DAY]        print the merged post for DAY (default yesterday);
#                          no Slack, no state, no lock
#
# Requires:
#   - $SLACK_CCUSAGE_WEBHOOK_URL set on the poster (incoming webhook for a Slack channel)
#   - `npx`, `jq`, `curl`, `python3` on PATH; `superset` on the poster when it has peers
#   - ccusage >= 20 for Codex data (older versions still post Claude-only)
#
# Table: one table for both providers, every model sorted by output tokens (most
# first), columns Model Cost Input Output, then one column per reasoning-effort
# level used that day (Min Low Med High XHigh Max), each row's cost split by
# effort as a percentage. Then one total row per provider (Claude, Codex). There
# are no cache columns and no cache line, so sparky's cache rankings have no
# Claude cache figures for these posts (a week with any such day drops out of
# them). The post is the header line plus the table; there is no subtotal line.
# ccusage has no effort field, so lib/effort-mix.py reads it from Claude Code's
# transcripts and Codex's rollouts; with no data the effort columns are left out.
# lib/merge-usage.py sums the machines (and weight-averages the effort split).
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
# Failures: a failed ccusage run, peer collection or Slack POST is appended to
# ~/.claude/.ccusage-state/errors.log and raises one macOS notification per
# day (nothing goes to Slack — the webhook channel feeds sparky's leaderboard).
# A half-installed ccusage in the npx cache ("native binary is not available")
# is evicted and retried once; npx reuses an entry that already matches
# `@latest`, so without that it fails silently on every session.

set -u  # NOT -e — graceful no-op on missing tools / transient failures

mode=post
case "${1:-}" in
  --collect) mode=collect ;;
  --dry-run) mode=dry-run ;;
esac

POSTER="${CCUSAGE_POSTER-jake1}"
PEERS="${CCUSAGE_PEERS-jake2}"
lib_dir="$(cd "$(dirname "$0")" && pwd -P)/lib"

WEBHOOK="${SLACK_CCUSAGE_WEBHOOK_URL:-}"
if [ "$mode" = post ] && [ -z "$WEBHOOK" ]; then
  echo "[ccusage-hook] SLACK_CCUSAGE_WEBHOOK_URL unset; skipping" >&2; exit 0
fi

for bin in npx jq; do
  command -v "$bin" >/dev/null 2>&1 || { echo "[ccusage-hook] $bin not on PATH; skipping" >&2; exit 0; }
done
if [ "$mode" = post ]; then
  command -v curl >/dev/null 2>&1 || { echo "[ccusage-hook] curl not on PATH; skipping" >&2; exit 0; }
fi

state_dir="$HOME/.claude/.ccusage-state"
last_file="$state_dir/last-posted"

# Yesterday in YYYY-MM-DD (BSD `date` on macOS; GNU `date` elsewhere)
yesterday=$(date -v-1d +%Y-%m-%d 2>/dev/null)
[ -n "$yesterday" ] || yesterday=$(date -d 'yesterday' +%Y-%m-%d)

date_pattern='^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
if [ "$mode" = post ]; then
  last_posted=""
  if [ -r "$last_file" ]; then
    read -r last_posted 2>/dev/null < "$last_file" || true
  fi
  if [[ "$last_posted" =~ $date_pattern ]] \
     && [[ "$last_posted" > "$yesterday" || "$last_posted" == "$yesterday" ]]; then
    exit 0
  fi
  # Not the designated poster: the poster collects this machine's usage itself.
  if [ -n "$POSTER" ] && [ "$(hostname -s 2>/dev/null)" != "$POSTER" ]; then
    exit 0
  fi
fi

err_file=$(mktemp "${TMPDIR:-/tmp}/ccusage-hook.XXXXXX" 2>/dev/null) || err_file=/dev/null
work_dir=$(mktemp -d "${TMPDIR:-/tmp}/ccusage-hook-work.XXXXXX" 2>/dev/null) || work_dir=""
lock=""
cleanup() {
  [ -n "$lock" ] && rmdir "$lock" 2>/dev/null
  [ "$err_file" = /dev/null ] || rm -f "$err_file"
  [ -n "$work_dir" ] && rm -rf "$work_dir"
  return 0
}
trap cleanup EXIT
[ -n "$work_dir" ] || { echo "[ccusage-hook] mktemp failed; skipping" >&2; exit 0; }

# A python3 that runs. A version-manager shim (mise, pyenv) can exit non-zero
# depending on the directory or HOME it is run from, so fall back to the system one.
PY=""
for py in python3 /usr/bin/python3; do
  command -v "$py" >/dev/null 2>&1 && "$py" -c pass >/dev/null 2>&1 && { PY="$py"; break; }
done

next_day() {
  date -j -v+1d -f "%Y-%m-%d" "$1" +%Y-%m-%d 2>/dev/null || date -d "$1 + 1 day" +%Y-%m-%d
}

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
# system one (see PY above); the post must not depend on a shim.
effort_mix() {
  local py out
  for py in python3 /usr/bin/python3; do
    command -v "$py" >/dev/null 2>&1 || continue
    out=$(printf '%s' "$1" | "$py" "$lib_dir/effort-mix.py" ${3:-} "$2" 2>/dev/null) && [ -n "$out" ] && { printf '%s' "$out"; return 0; }
  done
  printf '{}'
}

# json_or_empty <text> — the text if it is JSON, else {} (a parse failure then
# reads as "no activity" rather than breaking the merge).
json_or_empty() {
  if [ -n "$1" ] && printf '%s' "$1" | jq -e . >/dev/null 2>&1; then printf '%s' "$1"; else printf '{}'; fi
}

# collect_bundle <YYYY-MM-DD> — this machine's data for one day as
# {claude, codex, effort, codex_effort}. Fails (with stderr in $err_file) when
# the Claude report cannot be read; Codex failures degrade to {}.
collect_bundle() {
  local day=$1 compact=${1//-/} claude_json codex_json effort_json codex_effort_json
  # `ccusage claude daily` (not the top-level `ccusage daily`) is what still
  # emits `modelBreakdowns[]`. The top-level command went multi-agent and only
  # returns `modelsUsed` (a flat array of names), which collapses our table to
  # just the Total row. We make a second call for `codex daily` and merge in jq.
  # ccusage demands YYYYMMDD (no dashes).
  claude_json=$(run_ccusage claude daily --since "$compact" --until "$compact" --json) || return 1
  # Codex support landed in ccusage 20. On older versions the subcommand exits
  # with "Command not found: codex" — treat that (and any other failure) as an
  # empty result so we degrade to a Claude-only post instead of breaking.
  codex_json=$(run_ccusage codex daily --since "$compact" --until "$compact" --json) || codex_json='{}'
  claude_json=$(json_or_empty "$claude_json")
  codex_json=$(json_or_empty "$codex_json")
  # Reasoning-effort mix per model row, appended after Output (ccusage has no
  # effort field; Claude Code's transcripts do). Any failure or no data yields
  # {} and the table is exactly what it was before.
  effort_json=$(json_or_empty "$(effort_mix "$claude_json" "$day")")
  codex_effort_json=$(json_or_empty "$(effort_mix "$codex_json" "$day" --codex)")
  jq -nc --argjson c "$claude_json" --argjson x "$codex_json" \
    --argjson e "$effort_json" --argjson xe "$codex_effort_json" \
    '{claude: $c, codex: $x, effort: $e, codex_effort: $xe}'
}

# fetch_peer <superset host> <start> <end> <out file> — runs `--collect` on the
# peer in a throwaway Superset terminal and writes its {day: bundle} JSON to the
# out file. Fails when the host is offline, has no workspace, times out or
# returns a frame whose hash does not match. The terminal is always closed.
fetch_peer() {
  local host=$1 start=$2 end=$3 out=$4 hid ws term screen rc
  # The CLI installs to ~/.superset/bin, which only an interactive profile puts on PATH.
  local ss
  ss=$(command -v superset 2>/dev/null) || ss="$HOME/.superset/bin/superset"
  [ -x "$ss" ] || { echo "superset CLI not found" >&2; return 1; }
  [ -n "$PY" ] || { echo "no working python3" >&2; return 1; }
  # By id: `--host <name>` has reported a listed-online host as offline.
  hid=$("$ss" hosts list --json 2>/dev/null | jq -r --arg h "$host" \
    'first(.[] | select((.name == $h or .id == $h) and .online == "yes") | .id) // empty' 2>/dev/null)
  [ -n "$hid" ] || { echo "Superset host $host is offline or unknown" >&2; return 1; }
  ws=$("$ss" ws list --host "$hid" --json 2>/dev/null | jq -r \
    'first(.[] | select(.archivedAt == null and .worktreeExists != false) | .id) // empty' 2>/dev/null)
  [ -n "$ws" ] || { echo "no Superset workspace on $host to open a terminal in" >&2; return 1; }
  # The grep guard keeps a peer still on a script without --collect from running
  # a normal post (its own catch-up) instead.
  local remote="f=~/.claude/hooks/post-yesterdays-ccusage.sh; grep -q -- '--collect)' \$f && bash \$f --collect $start $end"
  term=$("$ss" terminals create --host "$hid" --workspace "$ws" --json --command "$remote" 2>/dev/null \
    | jq -r '.terminalId // empty' 2>/dev/null)
  [ -n "$term" ] || { echo "could not open a terminal on $host" >&2; return 1; }
  "$ss" terminals wait --host "$hid" --workspace "$ws" --terminal "$term" \
    --match 'CCUSAGE-BUNDLE-END [0-9a-f]{64}' --timeout "${CCUSAGE_PEER_TIMEOUT:-200}" >/dev/null 2>&1
  screen=$("$ss" terminals read --host "$hid" --workspace "$ws" --terminal "$term" --json 2>/dev/null \
    | jq -r '.text // empty' 2>/dev/null)
  "$ss" terminals close --host "$hid" --workspace "$ws" --terminal "$term" >/dev/null 2>&1
  printf '%s' "$screen" | "$PY" "$lib_dir/merge-usage.py" --unframe > "$out"; rc=$?
  [ "$rc" -eq 0 ] || { echo "no valid data frame from $host; screen tail:" >&2; printf '%s\n' "$screen" | tail -n 5 >&2; }
  return "$rc"
}

# report_failure <day> <what> — log it, and notify at most once per calendar day
# so a persistent failure doesn't re-alert on every session start. A dry run
# only prints it.
report_failure() {
  local msg="ccusage hook: $2 for $1" today
  echo "[ccusage-hook] $msg" >&2
  if [ "$mode" != post ]; then
    head -c 400 "$err_file" 2>/dev/null | sed 's/^/    /' >&2
    return 0
  fi
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

# --collect START [END]: print this machine's {day: bundle} for those days, framed
# for the poster to read off a terminal screen. A day whose Claude report fails
# is left out; the poster holds that day.
if [ "$mode" = collect ]; then
  start="${2:-}"; end="${3:-${2:-}}"
  [[ "$start" =~ $date_pattern && "$end" =~ $date_pattern ]] \
    || { echo "usage: $0 --collect YYYY-MM-DD [YYYY-MM-DD]" >&2; exit 2; }
  [ -n "$PY" ] || { echo "[ccusage-hook] no working python3" >&2; exit 1; }
  all='{}'
  current="$start"
  while [[ ! "$current" > "$end" ]]; do
    if bundle=$(collect_bundle "$current"); then
      all=$(jq -c --arg d "$current" --argjson b "$bundle" '. + {($d): $b}' <<<"$all")
    else
      echo "[ccusage-hook] ccusage claude failed for $current:" >&2
      head -c 400 "$err_file" >&2
    fi
    current=$(next_day "$current")
  done
  printf '%s' "$all" | "$PY" "$lib_dir/merge-usage.py" --frame
  exit 0
fi

# Who's posting — prefer the git global email, fall back to $USER@hostname.
# Bare email only, no name and no angle brackets: Slack shows "<...>" literally,
# and a name in front of the email rendered as "jnelken mailto:..." in Slack.
# Sparky keys the engineer on the email; the name is display-only, and it falls
# back to the email's local part when no post carries a name.
git_name=$(git config --global user.name 2>/dev/null || true)
git_email=$(git config --global user.email 2>/dev/null || true)
if [ -n "$git_email" ]; then
  who="$git_email"
elif [ -n "$git_email" ]; then
  who="$git_email"
elif [ -n "$git_name" ]; then
  who="$git_name"
else
  who="${USER:-unknown}@$(hostname -s 2>/dev/null || echo unknown)"
fi

# render_summary <pretty date> <bundle json> — the Slack message for one day.
render_summary() {
  local pretty=$1 bundle=$2
  jq -nr \
    --argjson c "$(jq -c '.claude // {}' <<<"$bundle")" \
    --argjson x "$(jq -c '.codex // {}' <<<"$bundle")" \
    --argjson e "$(jq -c '.effort // {}' <<<"$bundle")" \
    --argjson xe "$(jq -c '.codex_effort // {}' <<<"$bundle")" \
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
      # Header: day, who, grand total. The table carries everything else.
      "*\($d) — \($who): \(($ccost + $xcost) | money)*\n" +
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
  ' 2>/dev/null || printf '%s — %s: (parse error)' "$pretty" "$who"
}

# Days to post: --dry-run DAY (default yesterday), else last-posted+1 through yesterday.
if [ "$mode" = dry-run ]; then
  start="${2:-$yesterday}"; end="$start"
  [[ "$start" =~ $date_pattern ]] || { echo "usage: $0 --dry-run [YYYY-MM-DD]" >&2; exit 2; }
else
  mkdir -p "$state_dir" 2>/dev/null || exit 0
  # Atomic claim — keyed on yesterday so parallel sessions don't both catch up
  claim="$state_dir/.lock-$yesterday"
  # If the lock is stale (left by a SIGKILL'd async hook), remove it first.
  if [ -d "$claim" ]; then
    lock_age=$(( $(date +%s) - $(stat -f %m "$claim" 2>/dev/null || stat -c %Y "$claim" 2>/dev/null || echo 0) ))
    [ "$lock_age" -gt 7200 ] && rmdir "$claim" 2>/dev/null || true
  fi
  mkdir "$claim" 2>/dev/null || exit 0
  lock="$claim"
  # Start = day after last-posted, or yesterday if first run
  if last_posted=$(cat "$last_file" 2>/dev/null) && [ -n "$last_posted" ]; then
    start=$(next_day "$last_posted")
  else
    start="$yesterday"
  fi
  end="$yesterday"
  # Nothing to do if already caught up
  [[ "$start" > "$end" ]] && exit 0
fi

# Collect every peer's days in the background while this machine runs its own.
peer_pids=""
for host in $PEERS; do
  fetch_peer "$host" "$start" "$end" "$work_dir/peer-$host.json" 2>"$work_dir/peer-$host.err" &
  peer_pids="$peer_pids $host:$!"
done
peers_ready=""

current="$start"
while [[ ! "$current" > "$end" ]]; do
  # Pretty-printed date for display, e.g. "Wed, May 14". internal-tools'
  # sparky parses this header back out of Slack (weekday
  # prefix optional there, for backward compat with already-posted messages
  # in this old no-weekday shape) — see resolveUsageDate in
  # services/sparky/src/ccusageLeaderboard.ts.
  pretty=$(date -j -f "%Y-%m-%d" "$current" "+%a, %b %-d" 2>/dev/null \
        || date -d "$current" "+%a, %b %-d" 2>/dev/null \
        || printf '%s' "$current")

  collect_bundle "$current" > "$work_dir/local.json" || {
    report_failure "$current" "ccusage claude failed"
    break
  }

  # Every peer must answer for the day, or the day is held for a later session.
  if [ -z "$peers_ready" ]; then
    peers_ready=yes
    for entry in $peer_pids; do
      wait "${entry#*:}" || peers_ready=failed:"${entry%%:*}"
    done
  fi
  if [ "${peers_ready#failed:}" != "$peers_ready" ]; then
    cp "$work_dir/peer-${peers_ready#failed:}.err" "$err_file" 2>/dev/null
    report_failure "$current" "collecting from ${peers_ready#failed:} failed (will retry next session)"
    break
  fi
  bundles=("$work_dir/local.json")
  missing=""
  for host in $PEERS; do
    jq -e --arg d "$current" '.[$d]' "$work_dir/peer-$host.json" > "$work_dir/day-$host.json" 2>/dev/null \
      || { missing="$host"; break; }
    bundles+=("$work_dir/day-$host.json")
  done
  if [ -n "$missing" ]; then
    : > "$err_file"
    report_failure "$current" "$missing had no data (will retry next session)"
    break
  fi

  if [ "${#bundles[@]}" -gt 1 ]; then
    bundle=$("$PY" "$lib_dir/merge-usage.py" "${bundles[@]}" 2>"$err_file") || {
      report_failure "$current" "merging peer usage failed"
      break
    }
  else
    bundle=$(cat "$work_dir/local.json")
  fi
  summary=$(render_summary "$pretty" "$bundle")

  if [ "$mode" = dry-run ]; then
    printf '%s\n' "$summary"
    break
  fi

  # No Claude or Codex usage that day — skip the Slack post entirely (still
  # advance the marker so catchup doesn't re-check this day every session).
  if [[ "$summary" == "_"*" no activity_" ]]; then
    printf '%s' "$current" > "$last_file"
    current=$(next_day "$current")
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

  current=$(next_day "$current")
  sleep 1
done
wait 2>/dev/null
