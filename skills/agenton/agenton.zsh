# ---------------------------------------------------------------------------
# agenton / agentoff — keep the Mac (and Wi-Fi) awake with the lid closed so
# background agents keep running, and leave a per-minute connectivity log.
#
# Lives in ai-tools (skills/agenton); ~/dotfiles/zsh/lib/45-agenton.zsh sources it
# from the deploy clone. Edit it in the dev checkout, never via ~/.ai-tools.
#
#   agenton [MINUTES]   disable lid sleep for N minutes (default 5), then restore
#   agentoff            restore normal sleep immediately
#   agentlog [N]        tail the last N lines of the log (default 60)
#   claude_sessions [-v]  count live Claude Code sessions (busy/total); -v lists them
#
# Log: ~/Library/Logs/agenton.log  — one line per wall-clock minute:
#   Sep 10 - 2:03pm     🏢      WiFi ✓  83ms     claude: ⚡1/10  codex: ⚡1/2
#   Sep 10 - 2:04pm     📱      WiFi ✗           claude: ⚡2/10  codex: ⚡0/2
#
# Legend:
#   icon      the Wi-Fi network, named by AGENTON_NETWORKS (gateway-IP globs); an
#             unnamed network shows its gateway IP, 🚫 means no Wi-Fi
#   WiFi ✓    ping worked ("(http)" = ping blocked but HTTP worked); ✗ = neither
#   claude    ⚡busy/total live sessions, from Claude Code's own registry
#   codex     ⚡busy/total: live `codex` processes, busy from
#             ~/.codex/turn-activity.jsonl
#   others    any other agent comes from `superset snapshot` and only covers
#             Superset terminals
# A missing minute means the machine was asleep or the process was killed.
# ---------------------------------------------------------------------------

AGENTON_LOG="${AGENTON_LOG:-$HOME/Library/Logs/agenton.log}"

alias agentoff="sudo pmset -a disablesleep 0 && echo '✅ Lid sleep ENABLED. Mac will sleep normally now.'"
agentlog() { tail -n "${1:-60}" "$AGENTON_LOG"; }

# macOS redacts the SSID from every CLI tool (networksetup, ipconfig, wdutil and
# system_profiler, even as root) unless the caller has Location Services, so the
# network is identified by its gateway IP instead.
#
# Icons for gateway IPs. Each entry is "pattern=icon" where pattern is a zsh glob
# matched against the gateway IP (so "10.199.*" covers a whole subnet); first
# match wins, so put specific entries before broad ones. Keep icons one emoji
# wide so the log columns line up. Edit freely.
AGENTON_NETWORKS=(
    "10.199.*=🏢"
    "172.20.10.1=📱"
    "192.168.43.1=📱"
    "192.168.49.1=📱"
)

# Prints the Wi-Fi network's icon from AGENTON_NETWORKS, its gateway IP if no
# entry matches, or 🚫 when there is no gateway.
_agenton_network() {
    local gw entry
    gw=$(ipconfig getoption en0 router 2>/dev/null)
    [[ -n "$gw" ]] || { printf '🚫'; return; }
    for entry in "${AGENTON_NETWORKS[@]}"; do
        if [[ "$gw" == ${~entry%%=*} ]]; then
            printf '%s' "${entry#*=}"
            return
        fi
    done
    printf '%s' "$gw"
}

# Prints "WiFi ✓  <latency>ms", "WiFi ✓  (http)", or "WiFi ✗". Bounded to ~5s worst case.
_agenton_probe() {
    local out rtt
    if out=$(ping -c1 -W 2000 1.1.1.1 2>/dev/null); then
        rtt=${out##*min/avg/max/stddev = }
        rtt=${rtt%%/*}
        printf 'WiFi ✓  %.0fms' "$rtt"
        return 0
    fi
    if curl -sI --max-time 3 -o /dev/null -w '%{http_code}' \
            https://www.gstatic.com/generate_204 2>/dev/null | grep -q '^204$'; then
        printf 'WiFi ✓  (http)'
        return 0
    fi
    printf 'WiFi ✗'
    return 1
}

# "Sep 11 - 12:16pm" — one date call, am/pm lowercased in zsh.
_agenton_now() {
    local d=$(date '+%b %-d - %-I:%M|%p')
    printf '%s%s' "${d%|*}" "${(L)${d#*|}}"
}

_agenton_log() {
    mkdir -p "${AGENTON_LOG:h}"
    printf '%-21s%s\n' "$(_agenton_now)" "$*" >> "$AGENTON_LOG"
}

# Live Claude Code sessions, read from the registry Claude Code maintains at
# ~/.claude/sessions/<pid>.json (one file per interactive session, with a
# status field). Files whose pid is gone are ignored, so crashes don't inflate
# the count. Prints e.g. "⚡1/10" (busy/total); -v lists each one.
claude_sessions() {
    local verbose=0 f pid total=0 busy=0 waiting=0 idle=0 other=0 st
    [[ "$1" == "-v" ]] && verbose=1
    for f in "$HOME"/.claude/sessions/*.json(N); do
        pid=${f:t:r}
        kill -0 "$pid" 2>/dev/null || continue
        st=$(jq -r '.status // "unknown"' "$f" 2>/dev/null)
        (( total++ ))
        case "$st" in
            busy)    (( busy++ )) ;;
            waiting) (( waiting++ )) ;;
            idle)    (( idle++ )) ;;
            *)       (( other++ )) ;;
        esac
        if (( verbose )); then
            case "$st" in busy) printf '⚡ ';; waiting) printf '✋ ';; idle) printf '💤 ';; *) printf '?  ';; esac
            jq -r '"\(.name // "?")\t\(.cwd // "?")"' "$f" 2>/dev/null
        fi
    done
    (( verbose )) && echo "---"
    printf '⚡%d/%d\n' "$busy" "$total"
}

# Agents other than Claude and Codex in Superset terminals, from `superset
# snapshot`, which reports each terminal's agent id and state. Prints e.g.
# "cursor: ⚡1/2" (working/total), one entry per agent, or nothing if Superset is
# unavailable. Claude and Codex are excluded: they have their own sources that
# also cover sessions running outside Superset (claude_sessions, _agenton_codex).
_agenton_other_agents() {
    command -v superset >/dev/null && command -v jq >/dev/null || return 0
    perl -e 'alarm 5; exec @ARGV' superset snapshot --json 2>/dev/null | jq -r '
        [.workspaces[].terminals[]?.agent // empty | select(.id != "claude" and .id != "codex")]
        | group_by(.id)
        | map("\(.[0].id): ⚡\(map(select(.state == "working")) | length)/\(length)")
        | join("  ")' 2>/dev/null
}

# Live Codex sessions as "codex: ⚡busy/total", or nothing when none are running.
# Total is the live `codex` processes (pgrep -x skips the codex-code-mode-host
# helper), so it includes sessions outside Superset. Busy is the number of
# sessions whose latest event in ~/.codex/turn-activity.jsonl (UserPromptSubmit
# -> active, Stop -> idle) is "active". The log has no pid, so an interrupted
# turn that never wrote its Stop could stick on active: busy ignores events
# older than an hour and is capped at the live process count.
_agenton_codex() {
    local log="${CODEX_HOME:-$HOME/.codex}/turn-activity.jsonl" total busy=0 cutoff
    total=$(pgrep -x codex 2>/dev/null | wc -l | tr -d ' ')
    (( total > 0 )) || return 0
    if [[ -r "$log" ]]; then
        cutoff=$(date -u -v-60M '+%Y-%m-%dT%H:%M:%S')
        busy=$(tail -n 2000 "$log" | jq -Rn --arg cutoff "$cutoff" '
            reduce (inputs | fromjson?) as $e ({}; .[$e.session_id] = $e)
            | [.[] | select(.status == "active" and .timestamp[:19] >= $cutoff)] | length' 2>/dev/null)
        [[ "$busy" == <-> ]] || busy=0
    fi
    (( busy > total )) && busy=$total
    printf 'codex: ⚡%d/%d' "$busy" "$total"
}

# Everything after "claude: ..." on the line: Codex, then any other Superset agents.
_agenton_extra_agents() {
    local codex others
    codex=$(_agenton_codex)
    others=$(_agenton_other_agents)
    printf '%s' "$codex${codex:+${others:+  }}$others"
}

agent_timed() {
    local MINUTES=${1:-5}
    # End time comes from the clock, not a decrementing counter, so the
    # timer can't run long if the machine dozes anyway.
    local END_TS=$(( $(date +%s) + MINUTES * 60 ))
    local LAST_MINUTE="" NOW_MINUTE REMAINING probe net width others

    echo "🔒 [SUDO] Touch ID (or password) required to modify hardware sleep settings..."
    if ! sudo pmset -a disablesleep 1; then
        echo "❌ Error: Failed to acquire sudo privileges. Sleep settings unchanged."
        return 1
    fi

    echo "⚠️ Lid sleep DISABLED for ${MINUTES} minute(s). Wi-Fi and agents will stay active."
    echo
    _agenton_log "▶ agenton start (${MINUTES}m)"

    # Ctrl+C must still restore sleep and leave a marker in the log.
    trap '_agenton_log "✖ agenton interrupted"; sudo pmset -a disablesleep 0; echo "\n✅ Interrupted! Lid sleep re-enabled."; trap - INT; return 130' INT

    while (( $(date +%s) < END_TS )); do
        NOW_MINUTE=$(date '+%Y-%m-%d %H:%M')
        if [[ "$NOW_MINUTE" != "$LAST_MINUTE" ]]; then
            LAST_MINUTE=$NOW_MINUTE
            net=$(_agenton_network)
            # An emoji is one character but two columns wide; pad to 8 columns.
            width=${#net}; [[ "$net" == *[^[:ascii:]]* ]] && (( width++ ))
            others=$(_agenton_extra_agents)
            probe=$(printf '%s%*s%-16s claude: %s%s' "$net" $(( width < 8 ? 8 - width : 1 )) '' "$(_agenton_probe)" "$(claude_sessions)" "${others:+  $others}")
            _agenton_log "$probe"
            printf '\r\033[K%-21s%s\n' "$(_agenton_now)" "$probe"
        fi
        REMAINING=$(( END_TS - $(date +%s) ))
        (( REMAINING < 0 )) && REMAINING=0
        printf '\r⏳ Time remaining: %02d:%02d ' $(( REMAINING / 60 )) $(( REMAINING % 60 ))
        sleep 1
    done
    echo "\n"

    trap - INT
    _agenton_log "■ agenton finished"
    sudo pmset -a disablesleep 0
    echo "✅ Time up! Lid sleep re-enabled. Mac will now sleep normally."
}
alias agenton=agent_timed
