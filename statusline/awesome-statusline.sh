#!/bin/bash
# ============================================================================
# Awesome Statusline - Legacy 2.0.1
# ============================================================================
# Line 1: 🤖 Model 🪪 session-id | ✅ Git | 🐍 Env | 🎨 Style
# Line 2: 📂 full path 🌿(branch) | 💰 cost | ⏰ duration
# Line 3: 🧠 Context bar 20 blocks % used (tokens)
# Line 4: 🚀 Usage 5H bar 20 blocks % (Reset time)
# Line 5: ⭐ Usage 7D bar 20 blocks % (Reset day time), current 20% segment lit as it moves
# % numbers use gradient end color + Bold
# ============================================================================

# Ensure a bundled jq (copied next to this script by the installer) is found,
# even when Claude Code launches the statusline with a minimal PATH — common on
# Windows / GUI launches where a winget-installed jq is not on PATH yet.
export PATH="$(dirname "$0"):$PATH"

# Slurp stdin with builtins (no `cat` process). A line loop rather than
# `read -d ''`: with a tty on stdin bash puts the terminal in cbreak mode for -d,
# so ^D would never read as EOF.
input=""
while IFS= read -r _line || [[ -n "$_line" ]]; do input+="${_line}"$'\n'; done

# Parse all input fields and capture the clock in one jq process. @sh preserves
# empty fields and shell metacharacters; strip trailing newlines as the old
# per-field command substitutions did.
eval "$(jq -r '
    def chomp: if endswith("\n") then (.[:-1] | chomp) else . end;
    def assignment($name; $value):
        $name + "=" + ($value | if type == "string" then . else tostring end
                      | chomp | @sh);
    assignment("MODEL"; .model.display_name // "Unknown"),
    assignment("SESSION_ID"; .session_id // ""),
    assignment("CURRENT_DIR"; .workspace.current_dir // "."),
    assignment("CONTEXT_SIZE"; .context_window.context_window_size // 200000),
    assignment("OUTPUT_STYLE"; .output_style.name // ""),
    assignment("TOTAL_COST"; .cost.total_cost_usd // 0),
    assignment("TOTAL_DURATION"; .cost.total_duration_ms // 0),
    assignment("FIVE_HOUR_PCT"; .rate_limits.five_hour.used_percentage // ""),
    assignment("FIVE_HOUR_RESET"; .rate_limits.five_hour.resets_at // ""),
    assignment("SEVEN_DAY_PCT"; .rate_limits.seven_day.used_percentage // ""),
    assignment("SEVEN_DAY_RESET"; .rate_limits.seven_day.resets_at // ""),
    assignment("EFFORT"; .effort.level // ""),
    assignment("THINKING"; .thinking.enabled // ""),
    assignment("HAS_CURRENT_USAGE"; (.context_window.current_usage // null) != null),
    assignment("INPUT_TOKENS"; (.context_window.current_usage // {}).input_tokens // 0),
    assignment("CACHE_CREATE"; (.context_window.current_usage // {}).cache_creation_input_tokens // 0),
    assignment("CACHE_READ"; (.context_window.current_usage // {}).cache_read_input_tokens // 0),
    assignment("NOW"; now | floor)
' <<<"$input")"

# ============================================================================
# Colors
# ============================================================================
RESET=$'\033[0m'
BOLD=$'\033[1m'
ESC=$'\033'

cat_teal=$'\033[38;2;148;226;213m'
cat_pink=$'\033[38;2;245;194;231m'
cat_peach=$'\033[38;2;250;179;135m'
cat_green=$'\033[38;2;166;227;161m'
cat_subtext=$'\033[38;2;166;173;200m'
cat_lavender=$'\033[38;2;180;190;254m'
cat_yellow=$'\033[38;2;249;226;175m'
cat_overlay=$'\033[38;2;108;112;134m'
latte_green=$'\033[38;2;64;160;43m'
latte_red=$'\033[38;2;210;15;57m'
latte_yellow=$'\033[38;2;223;142;29m'

# ============================================================================
# Gradient Functions
# ============================================================================
# Unified gradient: Green (0%) → Yellow (75%) → Red (100%)
get_unified_gradient_color() {
    local pct=$1
    local r g b

    if [[ $pct -lt 75 ]]; then
        # Green (166;227;161) → Yellow (249;226;175)
        local t=$((pct * 100 / 75))
        r=$((166 + (249 - 166) * t / 100))
        g=$((227 + (226 - 227) * t / 100))
        b=$((161 + (175 - 161) * t / 100))
    else
        # Yellow (249;226;175) → Red (210;15;57)
        local t=$(((pct - 75) * 100 / 25))
        r=$((249 + (210 - 249) * t / 100))
        g=$((226 + (15 - 226) * t / 100))
        b=$((175 + (57 - 175) * t / 100))
    fi
    COLOR_OUT="$r;$g;$b"
}


# Usage gradient: Blue (0%) → Purple (bp%) → Red (100%). bp defaults to 75 (today's default);
# generate_bar's 5h/7d branch feeds a momentum-derived bp instead to shift the crossover point.
get_usage_unified_gradient_color() {
    local pct=$1
    local bp=${2:-75}
    [[ $bp -lt 1 ]] && bp=1
    [[ $bp -gt 99 ]] && bp=99
    local r g b

    if [[ $pct -lt $bp ]]; then
        # Blue (137;180;250) → Purple (203;166;247)
        local t=$((pct * 100 / bp))
        r=$((137 + (203 - 137) * t / 100))
        g=$((180 + (166 - 180) * t / 100))
        b=$((250 + (247 - 250) * t / 100))
    else
        # Purple (203;166;247) → Red (210;15;57)
        local t=$(( (pct - bp) * 100 / (100 - bp) ))
        r=$((203 + (210 - 203) * t / 100))
        g=$((166 + (15 - 166) * t / 100))
        b=$((247 + (57 - 247) * t / 100))
    fi
    COLOR_OUT="$r;$g;$b"
}

# Blend an "r;g;b" gradient string toward white by pct% - used to mark the 7D bar's
# 20% dividers (ruler ticks) as a lighter overlay without changing the underlying glyph.
lighten_rgb() {
    local rgb="$1" pct="$2"
    local r="${rgb%%;*}" rest="${rgb#*;}"
    local g="${rest%%;*}" b="${rest#*;}"
    r=$(( r + (255 - r) * pct / 100 ))
    g=$(( g + (255 - g) * pct / 100 ))
    b=$(( b + (255 - b) * pct / 100 ))
    COLOR_OUT="$r;$g;$b"
}

# Keep old functions for compatibility
get_context_gradient_color() {
    get_unified_gradient_color "$1"
}

get_usage_gradient_color() {
    get_usage_unified_gradient_color "$1"
}

get_usage_7d_gradient_color() {
    get_usage_unified_gradient_color "$1"
}

# Expected % of window "used" if usage tracked the clock exactly (5H only - see
# get_weekday_expected_pct for 7D, which fronts-loads the ramp within the same window):
# elapsed = window_seconds - remaining_seconds_to_reset, clamped to [0, window_seconds]
get_expected_pct() {
    local reset_epoch="$1" window_seconds="$2"
    [[ -z "$reset_epoch" || "$reset_epoch" == "null" ]] && { EXPECTED_PCT=0; return; }
    local remaining=$((reset_epoch - NOW))
    [[ $remaining -lt 0 ]] && remaining=0
    [[ $remaining -gt $window_seconds ]] && remaining=$window_seconds
    local elapsed=$((window_seconds - remaining))
    EXPECTED_PCT=$(( elapsed * 100 / window_seconds ))
}

# Expected % for the 7D bar: the weekly quota is a hard reset at resets_at (like 5H,
# just a 7-day cadence) - not a continuously-decaying rolling lookback. So real elapsed
# time since the last reset is well-defined; we just spread the ramp to 100% over the
# first workweek_seconds of that cycle (5 days) instead of the full 7, then hold flat -
# no budget reserved for the last 2 days before the next reset. Self-adjusts to whatever
# weekday/time your account's reset actually lands on; no calendar-day assumptions.
get_weekday_expected_pct() {
    local reset_epoch="$1" window_seconds="$2" workweek_seconds="$3"
    [[ -z "$reset_epoch" || "$reset_epoch" == "null" ]] && { EXPECTED_PCT=0; return; }
    local remaining=$((reset_epoch - NOW))
    [[ $remaining -lt 0 ]] && remaining=0
    [[ $remaining -gt $window_seconds ]] && remaining=$window_seconds
    local elapsed=$((window_seconds - remaining))
    if [[ $elapsed -ge $workweek_seconds ]]; then
        EXPECTED_PCT=100
    else
        EXPECTED_PCT=$(( elapsed * 100 / workweek_seconds ))
    fi
}

# Maps momentum (-cap..+cap) to a blue<->purple<->red crossover breakpoint (95..75..15):
# on pace -> 75 (today's default); behind pace -> up toward 95 (mostly blue, "safe");
# ahead of pace -> down toward 15 (mostly purple/red, "risky").
get_momentum_breakpoint() {
    local momentum=$1 cap=$2
    [[ $momentum -gt $cap ]] && momentum=$cap
    [[ $momentum -lt -$cap ]] && momentum=-$cap
    local bp
    if [[ $momentum -le 0 ]]; then
        bp=$(( 75 + (95 - 75) * (-momentum) / cap ))
    else
        bp=$(( 75 - (75 - 15) * momentum / cap ))
    fi
    MOMENTUM_BP="$bp"
}

generate_bar() {
    local pct=$1
    local width=$2
    local type=$3
    local momentum=${4:-0}
    local bar="" i
    local filled=$(( (pct * width + 50) / 100 ))
    [[ $filled -gt $width ]] && filled=$width

    if [[ "$type" == "5h" || "$type" == "7d" ]]; then
        # Positional blue->purple->red gradient; the crossover breakpoint shifts with momentum.
        # Background is a solid fill in the bar's own end color (same pattern as context below),
        # so 5H and 7D read as visually distinct instead of sharing one fixed backdrop.
        get_momentum_breakpoint "$momentum" "$MOMENTUM_CAP"
        local mom_bp="$MOMENTUM_BP"
        get_usage_unified_gradient_color "$pct" "$mom_bp"
        local end_color="$COLOR_OUT"
        local blink=""
        # SGR-5 blink for high-risk pace; many terminals (e.g. iTerm2 default profile,
        # Windows Terminal) ignore blink and just render solid color - fine as a fallback.
        [[ $momentum -ge $MOMENTUM_BLINK_THRESHOLD ]] && blink=$'\033[5m'
        # 7D only: light up whichever 20% segment the current pct falls in (4 columns),
        # tracking usage as it moves through the week - not a static grid. The window
        # jumps to the next segment as soon as pct crosses each 20/40/60/80% boundary,
        # so it doubles as a "how far into this fifth am I" indicator via the existing
        # filled/unfilled split inside the lit segment.
        local tick_every=$((width / 5))
        local cur_seg=$((pct / (100 / 5)))
        [[ $cur_seg -gt 4 ]] && cur_seg=4
        local seg_start=$((cur_seg * tick_every))
        local seg_end=$((seg_start + tick_every))
        local seg_lighten=32
        for ((i=0; i<filled; i++)); do
            local block_pct=$((i * 100 / width))
            get_usage_unified_gradient_color "$block_pct" "$mom_bp"
            local color="$COLOR_OUT"
            if [[ "$type" == "7d" && $i -ge $seg_start && $i -lt $seg_end ]]; then
                lighten_rgb "$color" "$seg_lighten"
                color="$COLOR_OUT"
            fi
            bar+="${blink}${ESC}[38;2;${color}m█"
        done
        # Explicitly clear blink (SGR-25) before the background - a color change alone doesn't
        # reset the blink attribute, so without this the unfilled tail would blink too.
        [[ -n "$blink" ]] && bar+=$'\033[25m'
        for ((i=0; i<width-filled; i++)); do
            local color="$end_color"
            local col=$((filled + i))
            if [[ "$type" == "7d" && $col -ge $seg_start && $col -lt $seg_end ]]; then
                lighten_rgb "$end_color" "$seg_lighten"
                color="$COLOR_OUT"
            fi
            bar+="${ESC}[38;2;${color}m░"
        done
    else
        get_context_gradient_color "$pct"
        local end_color="$COLOR_OUT"
        for ((i=0; i<filled; i++)); do
            local block_pct=$((i * 100 / width))
            get_context_gradient_color "$block_pct"
            bar+="${ESC}[38;2;${COLOR_OUT}m█"
        done
        for ((i=0; i<width-filled; i++)); do
            bar+="${ESC}[38;2;${end_color}m░"
        done
    fi

    BAR_OUT="$bar$RESET"
}

# Gradient text: shade each character from one RGB color to another
gradient_text() {
    local text="$1"
    local r1=$2 g1=$3 b1=$4
    local r2=$5 g2=$6 b2=$7
    local len=${#text}
    local out="" t r g b ch i

    for ((i=0; i<len; i++)); do
        ch="${text:$i:1}"
        t=0
        [[ $len -gt 1 ]] && t=$((i * 100 / (len - 1)))
        r=$((r1 + (r2 - r1) * t / 100))
        g=$((g1 + (g2 - g1) * t / 100))
        b=$((b1 + (b2 - b1) * t / 100))
        out+="${ESC}[38;2;${r};${g};${b}m${ch}"
    done

    GRADIENT_OUT="${out}${RESET}"
}

# ============================================================================
# Line 1: Model | Git Status | Env | Style
# ============================================================================

# Model (bold)
MODEL_DISPLAY="🤖 ${BOLD}${cat_teal}${MODEL}${RESET}"

# Reasoning effort + extended thinking (effort.level: low|medium|high|xhigh|max; absent if model lacks effort param)
[ -n "$EFFORT" ] && MODEL_DISPLAY="${MODEL_DISPLAY} \033[38;2;250;179;135m⚡${EFFORT}${RESET}"
[ "$THINKING" = "true" ] && MODEL_DISPLAY="${MODEL_DISPLAY} \033[38;2;249;226;175m💡${RESET}"

# Git status
GIT_STATUS_DISPLAY=""
cd "$CURRENT_DIR" 2>/dev/null
BRANCH=""
if GIT_OUT=$(git --no-optional-locks status --porcelain=v2 --branch --untracked-files=all 2>/dev/null); then
    STAGED=0 UNSTAGED=0 UNTRACKED=0
    while IFS= read -r entry; do
        case "$entry" in
            '# branch.head '*)
                BRANCH=${entry#\# branch.head }
                [[ "$BRANCH" == "(detached)" ]] && BRANCH=""
                ;;
            '1 '*|'2 '*)
                xy=${entry#??}
                xy=${xy%% *}
                [[ ${xy:0:1} != '.' ]] && STAGED=$((STAGED + 1))
                [[ ${xy:1:1} != '.' ]] && UNSTAGED=$((UNSTAGED + 1))
                ;;
            'u '*)
                # Unmerged: the cached diff lists a conflict once. The worktree
                # diff lists it once more when it also differs from stage 2,
                # which holds for a two-sided content conflict (conflict markers,
                # UU/AA); a delete-vs-modify conflict leaves our side untouched.
                STAGED=$((STAGED + 1))
                case "${entry:2:2}" in
                    UU|AA) UNSTAGED=$((UNSTAGED + 2)) ;;
                    *) UNSTAGED=$((UNSTAGED + 1)) ;;
                esac
                # Porcelain cannot see the worktree hash, so a UU/AA file edited
                # back to exactly stage 2 still counts !2 here (the old diff said !1).
                ;;
            '? '*) UNTRACKED=$((UNTRACKED + 1)) ;;
        esac
    done <<<"$GIT_OUT"

    if [[ "$STAGED" -eq 0 && "$UNSTAGED" -eq 0 && "$UNTRACKED" -eq 0 ]]; then
        GIT_STATUS_DISPLAY="${latte_green}✅ clean${RESET}"
    else
        STATUS=""
        [[ "$STAGED" -gt 0 ]] && STATUS="${STATUS}+${STAGED}"
        [[ "$UNSTAGED" -gt 0 ]] && STATUS="${STATUS}!${UNSTAGED}"
        [[ "$UNTRACKED" -gt 0 ]] && STATUS="${STATUS}?${UNTRACKED}"
        GIT_STATUS_DISPLAY="${latte_yellow}📝${STATUS}${RESET}"
    fi
else
    GIT_STATUS_DISPLAY="${cat_overlay}no git${RESET}"
fi

# Node.js version (Catppuccin Mocha Green)
ENV_DISPLAY=""
NODE_VER=""
np=$(command -v node)
if [[ -n "$np" ]]; then
    NODE_CACHE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/awesome-statusline"
    node_cache_key=${np//\//_}
    if [[ "$np" == */shims/* ]]; then
        node_cache_pwd=${PWD//\//_}
        node_cache_key="${node_cache_key}_${node_cache_pwd}"
    fi
    if [[ ${#node_cache_key} -gt 150 ]]; then
        # Include the entire key in a builtin checksum before truncation, so
        # long shim paths still distinguish working directories near the end.
        node_cache_hash=5381
        for ((node_cache_i=0; node_cache_i<${#node_cache_key}; node_cache_i++)); do
            printf -v node_cache_char '%d' "'${node_cache_key:node_cache_i:1}"
            node_cache_hash=$(( (node_cache_hash * 33 ^ node_cache_char) & 0xffffffff ))
        done
        node_cache_key="${node_cache_key:0:139}_${node_cache_hash}"
    fi
    NODE_CACHE_FILE="$NODE_CACHE_DIR/$node_cache_key"
    node_cache_ts="" node_cache_ver=""
    if [[ -f "$NODE_CACHE_FILE" && "$NODE_CACHE_FILE" -nt "$np" ]]; then
        read -r node_cache_ts node_cache_ver < "$NODE_CACHE_FILE"
        case "$node_cache_ts" in
            ''|*[!0-9]*) ;;
            *)
                if [[ -n "$node_cache_ver" ]] && (( NOW - 10#$node_cache_ts >= 0 && NOW - 10#$node_cache_ts < 86400 )); then
                    NODE_VER=$node_cache_ver
                fi
                ;;
        esac
    fi
    if [[ -z "$NODE_VER" ]]; then
        NODE_VER=$(node --version 2>/dev/null)
        if [[ -n "$NODE_VER" ]]; then
            [[ -d "$NODE_CACHE_DIR" ]] || mkdir -p "$NODE_CACHE_DIR" 2>/dev/null
            { printf '%s %s\n' "$NOW" "$NODE_VER" > "$NODE_CACHE_FILE"; } 2>/dev/null
        fi
    fi
fi
if [[ -n "$NODE_VER" ]]; then
    ENV_DISPLAY="⬢ ${cat_green}${NODE_VER}${RESET}"
else
    ENV_DISPLAY="${cat_overlay}no node${RESET}"
fi

# Output style
STYLE_DISPLAY=""
[[ -n "$OUTPUT_STYLE" ]] && STYLE_DISPLAY="🎨 ${cat_peach}${OUTPUT_STYLE}${RESET}"

# Session ID: kept on line 1, immediately after the model segment, so it survives
# a crash-restored terminal snapshot (only the top statusline rows are preserved)
# and lands before any soft-wrap on narrow terminals. Recovery tooling reads this
# to map a dead terminal buffer back to its resumable session.
SESSION_DISPLAY=""
[[ -n "$SESSION_ID" ]] && SESSION_DISPLAY=" ${cat_overlay}🪪${SESSION_ID}${RESET}"

LINE1="${MODEL_DISPLAY}${SESSION_DISPLAY} │ ${GIT_STATUS_DISPLAY} │ ${ENV_DISPLAY} │ ${STYLE_DISPLAY}"

# ============================================================================
# Line 2: Directory + Branch | Cost | Duration
# ============================================================================

# Directory: show the path, or the suffix after a UUID worktree directory
shorten_directory_path() {
    local path="$1"
    # Check if path contains a UUID pattern (worktrees)
    if [[ $path =~ /([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/(.+)$ ]]; then
        # Extract folder name after UUID
        local folder="${BASH_REMATCH[2]}"
        SHORTENED_DIR="$folder"
    else
        # No UUID, show full path
        SHORTENED_DIR="$path"
    fi
}

shorten_directory_path "$CURRENT_DIR"

if [[ -n "$BRANCH" && "$BRANCH" == "$SHORTENED_DIR" ]]; then
    # Worktree folder is named after its branch - collapse into one gradient indicator
    gradient_text "$SHORTENED_DIR" 223 142 29 64 160 43
    DIR_DISPLAY="⽊ ${GRADIENT_OUT}"
    BRANCH_DISPLAY=""
else
    # Escape backslashes: LINE2 is printed with `echo -e`, and a Windows path like
    # D:\code\... contains `\c` ("stop output"), which truncated the line at `D:` and
    # swallowed its newline so the context bar ran onto it. (The gradient branch above
    # only fires when the dir equals a branch name, which can't contain backslashes.)
    DIR_DISPLAY="⽊ ${latte_yellow}${SHORTENED_DIR//\\/\\\\}${RESET}"
    BRANCH_DISPLAY=""
    [[ -n "$BRANCH" ]] && BRANCH_DISPLAY=" ${latte_green}🌿(${BRANCH})${RESET}"
fi

# Cost
COST_DISPLAY=""
if [[ "$TOTAL_COST" != "0" && -n "$TOTAL_COST" ]]; then
    printf -v COST_FMT "%.2f" "$TOTAL_COST"
    COST_DISPLAY="💰 ${cat_subtext}${COST_FMT}\$${RESET}"
else
    COST_DISPLAY="💰 ${cat_subtext}0.00\$${RESET}"
fi

# Duration
DURATION_DISPLAY=""
if [[ "$TOTAL_DURATION" != "0" && -n "$TOTAL_DURATION" ]]; then
    DURATION_SEC=$((TOTAL_DURATION / 1000))
    if [[ $DURATION_SEC -ge 3600 ]]; then
        DURATION_FMT="$((DURATION_SEC / 3600))h$((DURATION_SEC % 3600 / 60))m"
    elif [[ $DURATION_SEC -ge 60 ]]; then
        DURATION_FMT="$((DURATION_SEC / 60))m"
    else
        DURATION_FMT="${DURATION_SEC}s"
    fi
    DURATION_DISPLAY="⏰ ${cat_subtext}${DURATION_FMT}${RESET}"
else
    DURATION_DISPLAY="⏰ ${cat_overlay}0s${RESET}"
fi

LINE2="${DIR_DISPLAY}${BRANCH_DISPLAY} │ ${COST_DISPLAY} │ ${DURATION_DISPLAY}"

# ============================================================================
# Line 3: Context (20 blocks)
# ============================================================================

CONTEXT_PERCENT=0
CURRENT_TOKENS=0
if [[ "$HAS_CURRENT_USAGE" == "true" ]]; then
    CURRENT_TOKENS=$((INPUT_TOKENS + CACHE_CREATE + CACHE_READ))
    [[ "$CONTEXT_SIZE" -gt 0 ]] && CONTEXT_PERCENT=$((CURRENT_TOKENS * 100 / CONTEXT_SIZE))
fi

# Format tokens as k
TOKENS_K=$((CURRENT_TOKENS / 1000))
CONTEXT_K=$((CONTEXT_SIZE / 1000))

generate_bar "$CONTEXT_PERCENT" 20 "context"
CTX_BAR=$BAR_OUT
LINE3="🧠 ${cat_pink}Context${RESET}  ${CTX_BAR} ${BOLD}${cat_subtext}${CONTEXT_PERCENT}% used${RESET} (${TOKENS_K}k/${CONTEXT_K}k)"

# ============================================================================
# Lines 4-5: Usage 5H and 7D (20 blocks)
# ============================================================================

# Format 5H reset as "Xh Xm left"
format_time_remaining() {
    local reset_epoch="$1"
    TIME_REMAINING_OUT=""
    [[ -z "$reset_epoch" || "$reset_epoch" == "null" ]] && return
    local remaining=$((reset_epoch - NOW))
    [[ $remaining -lt 0 ]] && remaining=0
    local hours=$((remaining / 3600))
    local minutes=$(((remaining % 3600) / 60))
    TIME_REMAINING_OUT="${hours}h${minutes}m left"
}

# Cross-platform date formatting (BSD/macOS vs GNU/Linux)
_date_fmt() {
    local epoch="$1" fmt="$2"
    DATE_OUT=""
    local out=""
    out=$(date -j -f "%s" "$epoch" "+$fmt" 2>/dev/null) && [[ -n "$out" ]] && { DATE_OUT="$out"; return; }
    out=$(date -r "$epoch" "+$fmt" 2>/dev/null) && [[ -n "$out" ]] && { DATE_OUT="$out"; return; }
    DATE_OUT=$(date -d "@$epoch" "+$fmt" 2>/dev/null)
}

# Format 7D reset as "Wed 14:00"
format_reset_datetime() {
    local reset_epoch="$1"
    DATE_OUT=""
    [[ -z "$reset_epoch" || "$reset_epoch" == "null" ]] && return
    LC_TIME=C _date_fmt "$reset_epoch" "%a %H:%M"
}

# Momentum: actual % used vs. expected %. 5H expects the clock to track linearly through
# the window; 7D expects the ramp to finish after workweek_seconds of the cycle instead
# of the full 7 days (see get_weekday_expected_pct).
FIVE_HOUR_WINDOW=18000    # 5 * 3600
SEVEN_DAY_WINDOW=604800   # 7 * 86400
WORKWEEK_SECONDS=432000   # 5 * 86400 - budget is expected to be fully spent by here
MOMENTUM_CAP=40           # momentum magnitude (pts) at which bar color fully saturates blue/red
MOMENTUM_BLINK_THRESHOLD=25

format_momentum() {
    local m=$1
    local r g b text
    if [[ $m -gt 0 ]]; then
        r=210; g=15; b=57; text="+${m}%"
    elif [[ $m -lt 0 ]]; then
        r=180; g=190; b=254; text="${m}%"
    else
        r=166; g=173; b=200; text="±0%"
    fi
    # Halve visual "opacity" by blending 50% toward the muted overlay gray (108;112;134),
    # the same de-emphasis color used elsewhere in this script (e.g. "no git", "(loading..)").
    r=$(( (r + 108) / 2 )); g=$(( (g + 112) / 2 )); b=$(( (b + 134) / 2 ))
    MOMENTUM_OUT="${ESC}[38;2;${r};${g};${b}m${text}${RESET}"
}

# Usage from rate_limits
if [[ -n "$FIVE_HOUR_PCT" ]]; then
    printf -v FIVE_HOUR "%.0f" "$FIVE_HOUR_PCT"
    printf -v SEVEN_DAY "%.0f" "${SEVEN_DAY_PCT:-0}"

    get_expected_pct "$FIVE_HOUR_RESET" "$FIVE_HOUR_WINDOW"
    FIVE_EXPECTED=$EXPECTED_PCT
    get_weekday_expected_pct "$SEVEN_DAY_RESET" "$SEVEN_DAY_WINDOW" "$WORKWEEK_SECONDS"
    SEVEN_EXPECTED=$EXPECTED_PCT
    FIVE_MOMENTUM=$((FIVE_HOUR - FIVE_EXPECTED))
    SEVEN_MOMENTUM=$((SEVEN_DAY - SEVEN_EXPECTED))

    format_time_remaining "$FIVE_HOUR_RESET"
    FIVE_RESET_FMT=$TIME_REMAINING_OUT
    format_reset_datetime "$SEVEN_DAY_RESET"
    SEVEN_RESET_FMT=$DATE_OUT

    generate_bar "$FIVE_HOUR" 20 "5h" "$FIVE_MOMENTUM"
    FIVE_BAR=$BAR_OUT
    generate_bar "$SEVEN_DAY" 20 "7d" "$SEVEN_MOMENTUM"
    SEVEN_BAR=$BAR_OUT

    format_momentum "$FIVE_MOMENTUM"
    FIVE_MOMENTUM_FMT=$MOMENTUM_OUT
    format_momentum "$SEVEN_MOMENTUM"
    SEVEN_MOMENTUM_FMT=$MOMENTUM_OUT
    LINE4="🚀 ${cat_lavender}Usage 5H${RESET} ${FIVE_BAR} ${BOLD}${cat_subtext}${FIVE_HOUR}%${RESET} ${FIVE_MOMENTUM_FMT} (Reset ${FIVE_RESET_FMT})"
    LINE5="⭐ ${cat_yellow}Usage 7D${RESET} ${SEVEN_BAR} ${BOLD}${cat_subtext}${SEVEN_DAY}%${RESET} ${SEVEN_MOMENTUM_FMT} (Reset ${SEVEN_RESET_FMT})"
else
    generate_bar 0 20 "5h"
    FIVE_BAR=$BAR_OUT
    generate_bar 0 20 "7d"
    SEVEN_BAR=$BAR_OUT
    get_usage_gradient_color 0
    FIVE_END_COLOR=$COLOR_OUT
    get_usage_7d_gradient_color 0
    SEVEN_END_COLOR=$COLOR_OUT
    LINE4="🚀 ${cat_lavender}Usage 5H${RESET} ${FIVE_BAR} ${BOLD}\033[38;2;${FIVE_END_COLOR}m0%${RESET} ${cat_overlay}(loading..)${RESET}"
    LINE5="⭐ ${cat_yellow}Usage 7D${RESET} ${SEVEN_BAR} ${BOLD}\033[38;2;${SEVEN_END_COLOR}m0%${RESET} ${cat_overlay}(loading..)${RESET}"
fi

# ============================================================================
# Write usage values to temp file for agent access
# ============================================================================

USAGE_FILE="$HOME/.claude/state/claude-usage.json"
[[ -d "${USAGE_FILE%/*}" ]] || mkdir -p "${USAGE_FILE%/*}" 2>/dev/null

# Mark actual rate_limits observations so agents never treat the loading display as headroom.
USAGE_SOURCE=placeholder
[[ -n "$FIVE_HOUR_PCT" || -n "$SEVEN_DAY_PCT" ]] && USAGE_SOURCE=rate_limits
USAGE_JSON=$(jq -n \
  --arg source "$USAGE_SOURCE" \
  --arg five_pct "${FIVE_HOUR_PCT:-}" \
  --arg seven_pct "${SEVEN_DAY_PCT:-}" \
  --arg five_reset "${FIVE_RESET_FMT:-unknown}" \
  --arg seven_reset "${SEVEN_RESET_FMT:-unknown}" \
  --arg five_reset_epoch "${FIVE_HOUR_RESET:-}" \
  --arg seven_reset_epoch "${SEVEN_DAY_RESET:-}" \
  --arg five_momentum "${FIVE_MOMENTUM:-0}" \
  --arg seven_momentum "${SEVEN_MOMENTUM:-0}" \
  '{
    timestamp: now,
    source: $source,
    five_hour: {
      used_percentage: ($five_pct | tonumber? // null),
      reset_at: $five_reset,
      reset_epoch: ($five_reset_epoch | tonumber? // null),
      momentum: ($five_momentum | tonumber)
    },
    seven_day: {
      used_percentage: ($seven_pct | tonumber? // null),
      reset_at: $seven_reset,
      reset_epoch: ($seven_reset_epoch | tonumber? // null),
      momentum: ($seven_momentum | tonumber)
    }
  }')

echo "$USAGE_JSON" > "$USAGE_FILE" 2>/dev/null

# ============================================================================
# Output
# ============================================================================
echo -e "$LINE1"
echo -e "$LINE2"
echo -e "$LINE3"
echo -e "$LINE4"
echo -e "$LINE5"
