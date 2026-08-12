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

input=$(cat)

# Parse JSON input
MODEL=$(echo "$input" | jq -r '.model.display_name // "Unknown"')
SESSION_ID=$(echo "$input" | jq -r '.session_id // empty')
CURRENT_DIR=$(echo "$input" | jq -r '.workspace.current_dir // "."')
CONTEXT_SIZE=$(echo "$input" | jq -r '.context_window.context_window_size // 200000')
CURRENT_USAGE=$(echo "$input" | jq -r '.context_window.current_usage // null')
OUTPUT_STYLE=$(echo "$input" | jq -r '.output_style.name // ""')
TOTAL_COST=$(echo "$input" | jq -r '.cost.total_cost_usd // 0')
TOTAL_DURATION=$(echo "$input" | jq -r '.cost.total_duration_ms // 0')

# Rate limits (official API - available for Pro/Max subscribers)
FIVE_HOUR_PCT=$(echo "$input" | jq -r '.rate_limits.five_hour.used_percentage // empty')
FIVE_HOUR_RESET=$(echo "$input" | jq -r '.rate_limits.five_hour.resets_at // empty')
SEVEN_DAY_PCT=$(echo "$input" | jq -r '.rate_limits.seven_day.used_percentage // empty')
SEVEN_DAY_RESET=$(echo "$input" | jq -r '.rate_limits.seven_day.resets_at // empty')

# ============================================================================
# Colors
# ============================================================================
RESET="\033[0m"
BOLD="\033[1m"

cat_teal() { echo -e "\033[38;2;148;226;213m"; }
cat_pink() { echo -e "\033[38;2;245;194;231m"; }
cat_peach() { echo -e "\033[38;2;250;179;135m"; }
cat_green() { echo -e "\033[38;2;166;227;161m"; }
cat_subtext() { echo -e "\033[38;2;166;173;200m"; }
cat_lavender() { echo -e "\033[38;2;180;190;254m"; }
cat_yellow() { echo -e "\033[38;2;249;226;175m"; }
cat_overlay() { echo -e "\033[38;2;108;112;134m"; }
latte_green() { echo -e "\033[38;2;64;160;43m"; }
latte_red() { echo -e "\033[38;2;210;15;57m"; }
latte_yellow() { echo -e "\033[38;2;223;142;29m"; }

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
    echo "$r;$g;$b"
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
    echo "$r;$g;$b"
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
    echo "$r;$g;$b"
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
    [[ -z "$reset_epoch" || "$reset_epoch" == "null" ]] && { echo 0; return; }
    local now_epoch=$(date +%s)
    local remaining=$((reset_epoch - now_epoch))
    [[ $remaining -lt 0 ]] && remaining=0
    [[ $remaining -gt $window_seconds ]] && remaining=$window_seconds
    local elapsed=$((window_seconds - remaining))
    echo $(( elapsed * 100 / window_seconds ))
}

# Expected % for the 7D bar: the weekly quota is a hard reset at resets_at (like 5H,
# just a 7-day cadence) - not a continuously-decaying rolling lookback. So real elapsed
# time since the last reset is well-defined; we just spread the ramp to 100% over the
# first workweek_seconds of that cycle (5 days) instead of the full 7, then hold flat -
# no budget reserved for the last 2 days before the next reset. Self-adjusts to whatever
# weekday/time your account's reset actually lands on; no calendar-day assumptions.
get_weekday_expected_pct() {
    local reset_epoch="$1" window_seconds="$2" workweek_seconds="$3"
    [[ -z "$reset_epoch" || "$reset_epoch" == "null" ]] && { echo 0; return; }
    local now_epoch=$(date +%s)
    local remaining=$((reset_epoch - now_epoch))
    [[ $remaining -lt 0 ]] && remaining=0
    [[ $remaining -gt $window_seconds ]] && remaining=$window_seconds
    local elapsed=$((window_seconds - remaining))
    if [[ $elapsed -ge $workweek_seconds ]]; then
        echo 100
    else
        echo $(( elapsed * 100 / workweek_seconds ))
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
    echo "$bp"
}

generate_bar() {
    local pct=$1
    local width=$2
    local type=$3
    local momentum=${4:-0}
    local bar=""
    local filled=$(( (pct * width + 50) / 100 ))
    [[ $filled -gt $width ]] && filled=$width

    if [[ "$type" == "5h" || "$type" == "7d" ]]; then
        # Positional blue->purple->red gradient; the crossover breakpoint shifts with momentum.
        # Background is a solid fill in the bar's own end color (same pattern as context below),
        # so 5H and 7D read as visually distinct instead of sharing one fixed backdrop.
        local mom_bp=$(get_momentum_breakpoint "$momentum" "$MOMENTUM_CAP")
        local end_color=$(get_usage_unified_gradient_color "$pct" "$mom_bp")
        local blink=""
        # SGR-5 blink for high-risk pace; many terminals (e.g. iTerm2 default profile,
        # Windows Terminal) ignore blink and just render solid color - fine as a fallback.
        [[ $momentum -ge $MOMENTUM_BLINK_THRESHOLD ]] && blink="\033[5m"
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
            local color=$(get_usage_unified_gradient_color "$block_pct" "$mom_bp")
            if [[ "$type" == "7d" && $i -ge $seg_start && $i -lt $seg_end ]]; then
                color=$(lighten_rgb "$color" "$seg_lighten")
            fi
            bar+="${blink}\033[38;2;${color}m█"
        done
        # Explicitly clear blink (SGR-25) before the background - a color change alone doesn't
        # reset the blink attribute, so without this the unfilled tail would blink too.
        [[ -n "$blink" ]] && bar+="\033[25m"
        for ((i=0; i<width-filled; i++)); do
            local color="$end_color"
            local col=$((filled + i))
            if [[ "$type" == "7d" && $col -ge $seg_start && $col -lt $seg_end ]]; then
                color=$(lighten_rgb "$end_color" "$seg_lighten")
            fi
            bar+="\033[38;2;${color}m░"
        done
    else
        local end_color=$(get_context_gradient_color "$pct")
        for ((i=0; i<filled; i++)); do
            local block_pct=$((i * 100 / width))
            bar+="\033[38;2;$(get_context_gradient_color "$block_pct")m█"
        done
        for ((i=0; i<width-filled; i++)); do
            bar+="\033[38;2;${end_color}m░"
        done
    fi

    echo -e "$bar$RESET"
}

# Gradient text: shade each character from one RGB color to another
gradient_text() {
    local text="$1"
    local r1=$2 g1=$3 b1=$4
    local r2=$5 g2=$6 b2=$7
    local len=${#text}
    local out="" t r g b ch

    for ((i=0; i<len; i++)); do
        ch="${text:$i:1}"
        t=0
        [[ $len -gt 1 ]] && t=$((i * 100 / (len - 1)))
        r=$((r1 + (r2 - r1) * t / 100))
        g=$((g1 + (g2 - g1) * t / 100))
        b=$((b1 + (b2 - b1) * t / 100))
        out+="\033[38;2;${r};${g};${b}m${ch}"
    done

    echo -e "${out}${RESET}"
}

# ============================================================================
# Line 1: Model | Git Status | Env | Style
# ============================================================================

# Model (bold)
MODEL_DISPLAY="🤖 ${BOLD}$(cat_teal)${MODEL}${RESET}"

# Reasoning effort + extended thinking (effort.level: low|medium|high|xhigh|max; absent if model lacks effort param)
EFFORT=$(echo "$input" | jq -r '.effort.level // empty')
THINKING=$(echo "$input" | jq -r '.thinking.enabled // empty')
[ -n "$EFFORT" ] && MODEL_DISPLAY="${MODEL_DISPLAY} \033[38;2;250;179;135m⚡${EFFORT}${RESET}"
[ "$THINKING" = "true" ] && MODEL_DISPLAY="${MODEL_DISPLAY} \033[38;2;249;226;175m💡${RESET}"

# Git status
GIT_STATUS_DISPLAY=""
cd "$CURRENT_DIR" 2>/dev/null
if git rev-parse --git-dir > /dev/null 2>&1; then
    STAGED=$(git diff --cached --name-only 2>/dev/null | wc -l | tr -d ' ')
    UNSTAGED=$(git diff --name-only 2>/dev/null | wc -l | tr -d ' ')
    UNTRACKED=$(git ls-files --others --exclude-standard 2>/dev/null | wc -l | tr -d ' ')

    if [[ "$STAGED" -eq 0 && "$UNSTAGED" -eq 0 && "$UNTRACKED" -eq 0 ]]; then
        GIT_STATUS_DISPLAY="$(latte_green)✅ clean${RESET}"
    else
        STATUS=""
        [[ "$STAGED" -gt 0 ]] && STATUS="${STATUS}+${STAGED}"
        [[ "$UNSTAGED" -gt 0 ]] && STATUS="${STATUS}!${UNSTAGED}"
        [[ "$UNTRACKED" -gt 0 ]] && STATUS="${STATUS}?${UNTRACKED}"
        GIT_STATUS_DISPLAY="$(latte_yellow)📝${STATUS}${RESET}"
    fi
else
    GIT_STATUS_DISPLAY="$(cat_overlay)no git${RESET}"
fi

# Node.js version (Catppuccin Mocha Green)
ENV_DISPLAY=""
NODE_VER="$(node --version 2>/dev/null)"
if [[ -n "$NODE_VER" ]]; then
    ENV_DISPLAY="⬢ $(cat_green)${NODE_VER}${RESET}"
else
    ENV_DISPLAY="$(cat_overlay)no node${RESET}"
fi

# Output style
STYLE_DISPLAY=""
[[ -n "$OUTPUT_STYLE" ]] && STYLE_DISPLAY="🎨 $(cat_peach)${OUTPUT_STYLE}${RESET}"

# Session ID: kept on line 1, immediately after the model segment, so it survives
# a crash-restored terminal snapshot (only the top statusline rows are preserved)
# and lands before any soft-wrap on narrow terminals. Recovery tooling reads this
# to map a dead terminal buffer back to its resumable session.
SESSION_DISPLAY=""
[[ -n "$SESSION_ID" ]] && SESSION_DISPLAY=" $(cat_overlay)🪪${SESSION_ID}${RESET}"

LINE1="${MODEL_DISPLAY}${SESSION_DISPLAY} │ ${GIT_STATUS_DISPLAY} │ ${ENV_DISPLAY} │ ${STYLE_DISPLAY}"

# ============================================================================
# Line 2: Directory + Branch | Cost | Duration
# ============================================================================

# Directory: shorten UUIDs, show just folder name
shorten_directory_path() {
    local path="$1"
    # Check if path contains a UUID pattern (worktrees)
    if [[ $path =~ /([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/(.+)$ ]]; then
        # Extract folder name after UUID
        local folder="${BASH_REMATCH[2]}"
        echo "$folder"
    else
        # No UUID, show full path
        echo "$path"
    fi
}

SHORTENED_DIR=$(shorten_directory_path "$CURRENT_DIR")

# Git branch
BRANCH=""
cd "$CURRENT_DIR" 2>/dev/null
if git rev-parse --git-dir > /dev/null 2>&1; then
    BRANCH=$(git branch --show-current 2>/dev/null)
fi

if [[ -n "$BRANCH" && "$BRANCH" == "$SHORTENED_DIR" ]]; then
    # Worktree folder is named after its branch - collapse into one gradient indicator
    DIR_DISPLAY="⽊ $(gradient_text "$SHORTENED_DIR" 223 142 29 64 160 43)"
    BRANCH_DISPLAY=""
else
    DIR_DISPLAY="⽊ $(latte_yellow)${SHORTENED_DIR}${RESET}"
    BRANCH_DISPLAY=""
    [[ -n "$BRANCH" ]] && BRANCH_DISPLAY=" $(latte_green)🌿(${BRANCH})${RESET}"
fi

# Cost
COST_DISPLAY=""
if [[ "$TOTAL_COST" != "0" && -n "$TOTAL_COST" ]]; then
    COST_FMT=$(printf "%.2f" "$TOTAL_COST")
    COST_DISPLAY="💰 $(cat_subtext)${COST_FMT}\$${RESET}"
else
    COST_DISPLAY="💰 $(cat_subtext)0.00\$${RESET}"
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
    DURATION_DISPLAY="⏰ $(cat_subtext)${DURATION_FMT}${RESET}"
else
    DURATION_DISPLAY="⏰ $(cat_overlay)0s${RESET}"
fi

LINE2="${DIR_DISPLAY}${BRANCH_DISPLAY} │ ${COST_DISPLAY} │ ${DURATION_DISPLAY}"

# ============================================================================
# Line 3: Context (20 blocks)
# ============================================================================

CONTEXT_PERCENT=0
CURRENT_TOKENS=0
if [[ "$CURRENT_USAGE" != "null" && -n "$CURRENT_USAGE" ]]; then
    INPUT_TOKENS=$(echo "$CURRENT_USAGE" | jq -r '.input_tokens // 0')
    CACHE_CREATE=$(echo "$CURRENT_USAGE" | jq -r '.cache_creation_input_tokens // 0')
    CACHE_READ=$(echo "$CURRENT_USAGE" | jq -r '.cache_read_input_tokens // 0')
    CURRENT_TOKENS=$((INPUT_TOKENS + CACHE_CREATE + CACHE_READ))
    [[ "$CONTEXT_SIZE" -gt 0 ]] && CONTEXT_PERCENT=$((CURRENT_TOKENS * 100 / CONTEXT_SIZE))
fi

# Format tokens as k
TOKENS_K=$((CURRENT_TOKENS / 1000))
CONTEXT_K=$((CONTEXT_SIZE / 1000))

CTX_BAR=$(generate_bar "$CONTEXT_PERCENT" 20 "context")
LINE3="🧠 $(cat_pink)Context${RESET}  ${CTX_BAR} ${BOLD}$(cat_subtext)${CONTEXT_PERCENT}% used${RESET} (${TOKENS_K}k/${CONTEXT_K}k)"

# ============================================================================
# Lines 4-5: Usage 5H and 7D (20 blocks)
# ============================================================================

# Format 5H reset as "Xh Xm left"
format_time_remaining() {
    local reset_epoch="$1"
    [[ -z "$reset_epoch" || "$reset_epoch" == "null" ]] && return
    local now_epoch=$(date +%s)
    local remaining=$((reset_epoch - now_epoch))
    [[ $remaining -lt 0 ]] && remaining=0
    local hours=$((remaining / 3600))
    local minutes=$(((remaining % 3600) / 60))
    echo "${hours}h${minutes}m left"
}

# Cross-platform date formatting (BSD/macOS vs GNU/Linux)
_date_fmt() {
    local epoch="$1" fmt="$2"
    local out=""
    out=$(date -j -f "%s" "$epoch" "+$fmt" 2>/dev/null) && [[ -n "$out" ]] && { echo "$out"; return; }
    out=$(date -r "$epoch" "+$fmt" 2>/dev/null) && [[ -n "$out" ]] && { echo "$out"; return; }
    date -d "@$epoch" "+$fmt" 2>/dev/null
}

# Format 7D reset as "Wed 14:00"
format_reset_datetime() {
    local reset_epoch="$1"
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
    echo -e "\033[38;2;${r};${g};${b}m${text}${RESET}"
}

# Usage from rate_limits
if [[ -n "$FIVE_HOUR_PCT" ]]; then
    FIVE_HOUR=$(printf "%.0f" "$FIVE_HOUR_PCT")
    SEVEN_DAY=$(printf "%.0f" "${SEVEN_DAY_PCT:-0}")

    FIVE_EXPECTED=$(get_expected_pct "$FIVE_HOUR_RESET" "$FIVE_HOUR_WINDOW")
    SEVEN_EXPECTED=$(get_weekday_expected_pct "$SEVEN_DAY_RESET" "$SEVEN_DAY_WINDOW" "$WORKWEEK_SECONDS")
    FIVE_MOMENTUM=$((FIVE_HOUR - FIVE_EXPECTED))
    SEVEN_MOMENTUM=$((SEVEN_DAY - SEVEN_EXPECTED))

    FIVE_RESET_FMT=$(format_time_remaining "$FIVE_HOUR_RESET")
    SEVEN_RESET_FMT=$(format_reset_datetime "$SEVEN_DAY_RESET")

    FIVE_BAR=$(generate_bar "$FIVE_HOUR" 20 "5h" "$FIVE_MOMENTUM")
    SEVEN_BAR=$(generate_bar "$SEVEN_DAY" 20 "7d" "$SEVEN_MOMENTUM")

    LINE4="🚀 $(cat_lavender)Usage 5H${RESET} ${FIVE_BAR} ${BOLD}$(cat_subtext)${FIVE_HOUR}%${RESET} $(format_momentum "$FIVE_MOMENTUM") (Reset ${FIVE_RESET_FMT})"
    LINE5="⭐ $(cat_yellow)Usage 7D${RESET} ${SEVEN_BAR} ${BOLD}$(cat_subtext)${SEVEN_DAY}%${RESET} $(format_momentum "$SEVEN_MOMENTUM") (Reset ${SEVEN_RESET_FMT})"
else
    FIVE_BAR=$(generate_bar 0 20 "5h")
    SEVEN_BAR=$(generate_bar 0 20 "7d")
    FIVE_END_COLOR=$(get_usage_gradient_color 0)
    SEVEN_END_COLOR=$(get_usage_7d_gradient_color 0)
    LINE4="🚀 $(cat_lavender)Usage 5H${RESET} ${FIVE_BAR} ${BOLD}\033[38;2;${FIVE_END_COLOR}m0%${RESET} $(cat_overlay)(loading..)${RESET}"
    LINE5="⭐ $(cat_yellow)Usage 7D${RESET} ${SEVEN_BAR} ${BOLD}\033[38;2;${SEVEN_END_COLOR}m0%${RESET} $(cat_overlay)(loading..)${RESET}"
fi

# ============================================================================
# Output
# ============================================================================
echo -e "$LINE1"
echo -e "$LINE2"
echo -e "$LINE3"
echo -e "$LINE4"
echo -e "$LINE5"
