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
# State: ~/.claude/.ccusage-state/last-posted holds the most recent
# YYYY-MM-DD that was successfully posted. Catchup posts every day from
# last+1 through yesterday, one message per day. The marker advances only
# after each successful POST, so transient Slack failures resume cleanly.

set -u  # NOT -e — graceful no-op on missing tools / transient failures

WEBHOOK="${SLACK_CCUSAGE_WEBHOOK_URL:-}"
[ -z "$WEBHOOK" ] && { echo "[ccusage-hook] SLACK_CCUSAGE_WEBHOOK_URL unset; skipping" >&2; exit 0; }

for bin in npx jq curl; do
  command -v "$bin" >/dev/null 2>&1 || { echo "[ccusage-hook] $bin not on PATH; skipping" >&2; exit 0; }
done

state_dir="$HOME/.claude/.ccusage-state"
last_file="$state_dir/last-posted"
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

# Yesterday in YYYY-MM-DD (BSD `date` on macOS; GNU `date` elsewhere)
yesterday=$(date -v-1d +%Y-%m-%d 2>/dev/null || date -d 'yesterday' +%Y-%m-%d)

# Atomic claim — keyed on yesterday so parallel sessions don't both catch up
lock="$state_dir/.lock-$yesterday"
# If the lock is stale (left by a SIGKILL'd async hook), remove it first.
if [ -d "$lock" ]; then
  lock_age=$(( $(date +%s) - $(stat -f %m "$lock" 2>/dev/null || stat -c %Y "$lock" 2>/dev/null || echo 0) ))
  [ "$lock_age" -gt 7200 ] && rmdir "$lock" 2>/dev/null || true
fi
mkdir "$lock" 2>/dev/null || exit 0
trap 'rmdir "$lock" 2>/dev/null || true' EXIT

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
  # Pretty-printed date for display, e.g. "May 14"
  pretty=$(date -j -f "%Y-%m-%d" "$current" "+%b %-d" 2>/dev/null \
        || date -d "$current" "+%b %-d" 2>/dev/null \
        || printf '%s' "$current")
  # `ccusage claude daily` (not the top-level `ccusage daily`) is what still
  # emits `modelBreakdowns[]`. The top-level command went multi-agent and only
  # returns `modelsUsed` (a flat array of names), which collapses our table to
  # just the Total row. We make a second call for `codex daily` and merge in jq.
  claude_json=$(npx -y ccusage@latest claude daily --since "$compact" --until "$compact" --json 2>/dev/null) || {
    echo "[ccusage-hook] ccusage claude failed for $current" >&2
    break
  }
  # Codex support landed in ccusage 20. On older versions the subcommand exits
  # with "Command not found: codex" — treat that (and any other failure) as an
  # empty result so we degrade to a Claude-only post instead of breaking.
  codex_json=$(npx -y ccusage@latest codex daily --since "$compact" --until "$compact" --json 2>/dev/null) || codex_json='{}'
  [ -z "$codex_json" ] && codex_json='{}'

  summary=$(jq -nr \
    --argjson c "$claude_json" \
    --argjson x "$codex_json" \
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
      (capture("claude-(?<fam>opus|sonnet|haiku)-(?<v>\\d+-\\d+)") // null) as $m |
      if $m then "\($m.fam | cap) \($m.v | gsub("-"; "."))" else . end;
    def lpad($n):
      tostring as $s | ($n - ($s | length)) as $p |
      (if $p > 0 then (" " * $p) else "" end) + $s;
    def rpad($n):
      tostring as $s | ($n - ($s | length)) as $p |
      $s + (if $p > 0 then (" " * $p) else "" end);
    # Row builder: model(12, left)  cost(7, right)  in(7, right)  out(7, right)  cache(7, right)
    def row(model; cost; tin; tout; cache):
      (model | rpad(12)) + "  " +
      (cost  | lpad(7))  + "  " +
      (tin   | lpad(7))  + "  " +
      (tout  | lpad(7))  + "  " +
      (cache | lpad(7));
    def sep: ("─" * 50);

    # Pull the single-day row from each report. ccusage emits {"daily": [...]}
    # for days with data, bare [] for days with none, and we coerce a parse
    # failure to {} so the "no activity" branch fires cleanly.
    ((if ($c | type) == "object" then ($c.daily // []) else ($c // []) end) | (.[0] // {})) as $cr |
    ((if ($x | type) == "object" then ($x.daily // []) else ($x // []) end) | (.[0] // {})) as $xr |

    ($cr != {}) as $chas |
    ($xr != {}) as $xhas |
    ($cr.totalCost // 0) as $ccost |
    ($xr.costUSD   // 0) as $xcost |

    if ($chas | not) and ($xhas | not) then
      "_ccusage \($d) — \($who): no activity_"
    else
      # Header: grand total, sub-totals beneath
      "*ccusage \($d) — \($who): \(($ccost + $xcost) | money)*\n" +
      "_Claude " + (if $chas then ($ccost | money) else "none" end) +
      " · Codex "  + (if $xhas then ($xcost | money) else "none" end) + "_\n" +
      "```\n" +

      # ── Claude block ───────────────────────────────────────────────
      (if $chas then
        ($cr.modelBreakdowns // []) as $cm |
        (($cr.cacheCreationTokens // 0) + ($cr.cacheReadTokens // 0)) as $ccache |
        "Claude\n" +
        row("Model"; "Cost"; "Input"; "Output"; "Cache") + "\n" +
        (if ($cm | length) > 0
          then ($cm | map(
            ((.cacheCreationTokens // 0) + (.cacheReadTokens // 0)) as $mc |
            row(.modelName | claudeModel;
                .cost // 0 | money;
                .inputTokens // 0 | fmt;
                .outputTokens // 0 | fmt;
                $mc | fmt)
          ) | join("\n")) + "\n" + sep + "\n"
          else "" end) +
        row("Claude";
            $ccost | money;
            $cr.inputTokens // 0 | fmt;
            $cr.outputTokens // 0 | fmt;
            $ccache | fmt)
       else "Claude: no activity" end) +
      "\n\n" +

      # ── Codex block ────────────────────────────────────────────────
      # Per-model cost is not exposed in `ccusage codex --json` (the Rust
      # adapter aggregates cost at the day level only). Per-model rows leave
      # the Cost column blank; the Codex total row carries the dollar figure.
      (if $xhas then
        ($xr.models // {}) as $xm |
        ($xr.cachedInputTokens // 0) as $xcache |
        "Codex\n" +
        row("Model"; "Cost"; "Input"; "Output"; "Cache") + "\n" +
        (if ($xm | length) > 0
          then ($xm | to_entries | map(
            row(.key;
                "";
                .value.inputTokens // 0 | fmt;
                .value.outputTokens // 0 | fmt;
                .value.cachedInputTokens // 0 | fmt)
          ) | join("\n")) + "\n" + sep + "\n"
          else "" end) +
        row("Codex";
            $xcost | money;
            $xr.inputTokens // 0 | fmt;
            $xr.outputTokens // 0 | fmt;
            $xr.cachedInputTokens // 0 | fmt)
       else "Codex: no activity" end) +
      "\n```"
    end
  ' 2>/dev/null) || summary="ccusage $pretty — $who: (parse error)"

  payload=$(jq -n --arg t "$summary" '{text: $t}')
  if curl -sf -X POST -H 'Content-Type: application/json' -d "$payload" "$WEBHOOK" > /dev/null; then
    printf '%s' "$current" > "$last_file"   # advance ONLY on success
  else
    echo "[ccusage-hook] slack POST failed for $current; will retry next session" >&2
    break
  fi

  current=$(date -j -v+1d -f "%Y-%m-%d" "$current" +%Y-%m-%d 2>/dev/null \
         || date -d "$current + 1 day" +%Y-%m-%d)
  sleep 1
done
