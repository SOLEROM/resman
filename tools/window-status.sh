#!/usr/bin/env bash
# window-status.sh — compute the local (daily) window + weekly cycle the same
# way the control plane does, from the shell, on demand.
#
# This mirrors control-plane/modules/window_schedule.py (WindowSchedule.status):
# it reads the same config/window_schedule.json, tiles the day into fixed-length
# windows, finds the current/next window, and derives weekly-cycle progress —
# so you can sanity-check the footer meters without the server running.
#
# Two figures per row, matching the footer meters:
#   * time  — how much of the window / week has elapsed (the bar fill + the
#             number drawn inside the bar).
#   * limit — how much of your usage limit is consumed. Fetched the same way
#             the server does: GET https://claude.ai/api/oauth/usage with the
#             operator's OAuth token (~/.claude/.credentials.json) — read-only,
#             no token spend. session = five_hour, weekly = seven_day. Shows "?"
#             when logged out / unreachable, or with --no-fetch.
#
# Usage:
#   tools/window-status.sh                       # now, default config, fetch limits
#   tools/window-status.sh --now "2026-06-12 22:30"   # pretend it's this time
#   tools/window-status.sh --no-fetch            # skip the claude.ai call
#   tools/window-status.sh --config-dir /tmp/resman-smoke
#   tools/window-status.sh --config /path/to/window_schedule.json
#
# Override the creds file with CLD20_CREDS_PATH. Times are in the server's
# local timezone, same as the app.
set -euo pipefail

# ----- defaults (must match window_schedule.py) -----
DEFAULT_STARTS=(0 5 10 15 20)
DEFAULT_LENGTH=5
DEFAULT_WD=0      # Monday (Python weekday convention: Mon=0 .. Sun=6)
DEFAULT_HR=0
WEEKDAY_NAMES=(Monday Tuesday Wednesday Thursday Friday Saturday Sunday)

# Repo root = parent of this tools/ dir; default config mirrors server.py
# (CONFIG_DIR = RESMAN_ROOT/config, file = window_schedule.json).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
CONFIG="$REPO_ROOT/config/window_schedule.json"
NOW_ARG=""
FETCH=1

# ----- args -----
while [[ $# -gt 0 ]]; do
  case "$1" in
    --config)      CONFIG="$2"; shift 2 ;;
    --config-dir)  CONFIG="$2/window_schedule.json"; shift 2 ;;
    --now)         NOW_ARG="$2"; shift 2 ;;
    --no-fetch)    FETCH=0; shift ;;
    -h|--help)     grep '^#' "$0" | grep -v '^#!' | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown arg: $1" >&2; exit 64 ;;
  esac
done

# ----- load config (jq if available + file present, else defaults) -----
STARTS=("${DEFAULT_STARTS[@]}"); NIGHTS=(); LENGTH=$DEFAULT_LENGTH
WD=$DEFAULT_WD; HR=$DEFAULT_HR; SRC="built-in defaults"
for _ in "${DEFAULT_STARTS[@]}"; do NIGHTS+=("false"); done

if [[ -f "$CONFIG" ]] && command -v jq >/dev/null 2>&1; then
  mapfile -t _starts < <(jq -r '.windows | sort_by(.server_start) | .[].server_start' "$CONFIG" 2>/dev/null || true)
  if [[ ${#_starts[@]} -gt 0 ]]; then
    STARTS=("${_starts[@]}")
    mapfile -t NIGHTS < <(jq -r '.windows | sort_by(.server_start) | .[].night_window' "$CONFIG")
    LENGTH=$(jq -r '.window_length_hours // 5' "$CONFIG")
    WD=$(jq -r '.weekly_anchor.weekday // 0' "$CONFIG")
    HR=$(jq -r '.weekly_anchor.hour // 0' "$CONFIG")
    SRC="$CONFIG"
  fi
fi
COUNT=${#STARTS[@]}
LENGTH_S=$((LENGTH * 3600))

# ----- now -----
if [[ -n "$NOW_ARG" ]]; then
  NOW=$(date -d "$NOW_ARG" +%s) || { echo "bad --now: $NOW_ARG" >&2; exit 64; }
else
  NOW=$(date +%s)
fi
TODAY=$(date -d "@$NOW" +%Y-%m-%d)

# ----- helpers -----
# Round 100*elapsed/total, clamped to 0..100 (matches Math.round in the UI).
pct() { awk -v e="$1" -v t="$2" 'BEGIN{ if(t<=0){print 0;exit} p=100*e/t; if(p<0)p=0; if(p>100)p=100; printf "%d", p+0.5 }'; }
hm()  { local s=$1; ((s<0)) && s=0; local h=$((s/3600)) m=$(((s%3600)/60)); if ((h>0)); then echo "${h}h ${m}m"; elif ((m>0)); then echo "${m}m"; else echo "${s}s"; fi; }
clk() { date -d "@$1" +%H:%M; }
bar() { # bar <pct> -> [#####.....] 20 cells
  local i fill=$(( $1 / 5 )) out="["
  for ((i=0;i<20;i++)); do if ((i<fill)); then out+="#"; else out+="."; fi; done
  echo "$out]"
}

# ----- find current + next window across day offsets (-1..7), like the server -----
cur_found=0; next_start=-1; cur_start=0; cur_end=0; cur_i=0; cur_night=false; next_night=false
for day_off in -1 0 1 2 3 4 5 6 7; do
  day_date=$(date -d "$TODAY $day_off day" +%Y-%m-%d)
  midnight=$(date -d "$day_date 00:00:00" +%s)
  for ((i=0; i<COUNT; i++)); do
    h=${STARTS[$i]}
    start=$((midnight + h*3600))
    end=$((start + LENGTH_S))
    if [[ "$start" -le "$NOW" && "$NOW" -lt "$end" ]]; then
      cur_found=1; cur_i=$((i+1)); cur_start=$start; cur_end=$end; cur_night=${NIGHTS[$i]}
    fi
    if [[ "$start" -gt "$NOW" ]]; then
      if [[ "$next_start" -lt 0 || "$start" -lt "$next_start" ]]; then
        next_start=$start; next_night=${NIGHTS[$i]}
      fi
    fi
  done
done

# ----- weekly cycle (mirror _weekly) -----
NOW_PYWD=$(( $(date -d "@$NOW" +%u) - 1 ))        # %u: Mon=1..Sun=7 -> Mon=0..Sun=6
DAYS_SINCE=$(( ( (NOW_PYWD - WD) % 7 + 7 ) % 7 ))
ws_date=$(date -d "$TODAY -$DAYS_SINCE day" +%Y-%m-%d)
WS=$(date -d "$ws_date $HR:00:00" +%s)
(( WS > NOW )) && WS=$(( WS - 7*86400 ))           # anchor in the future -> last week
WE=$(( WS + 7*86400 ))
WEEK_PCT=$(pct $((NOW - WS)) $((WE - WS)))
WEEK_LEFT=$(( WE - NOW )); (( WEEK_LEFT < 0 )) && WEEK_LEFT=0

# ----- usage limits (same source + code path as the server) -----
# Delegates to control-plane/modules/claude_usage.py — the SAME module the
# server uses, which shells out to `bun` for the GET. claude.ai sits behind
# Cloudflare bot management that fingerprints the client's TLS handshake
# (JA3/JA4) + HTTP/2 — NOT the User-Agent — so curl and Python's urllib both
# get a 403 "Just a moment…" challenge, while bun's browser-grade fetch passes.
# It GETs https://claude.ai/api/oauth/usage with the operator's OAuth token
# (~/.claude/.credentials.json, override CLD20_CREDS_PATH) — read-only, no token
# spend. session = five_hour.utilization, weekly = seven_day.utilization.
SESS_LIMIT="?"; WEEK_LIMIT="?"; LIMIT_NOTE="not fetched (--no-fetch)"
fetch_limits() {
  local py=""
  for cand in python3 python; do command -v "$cand" >/dev/null 2>&1 && { py="$cand"; break; }; done
  [[ -n "$py" ]] || { LIMIT_NOTE="python3 not found"; return; }
  local cp="$REPO_ROOT/control-plane"
  [[ -f "$cp/modules/claude_usage.py" ]] || { LIMIT_NOTE="claude_usage module not found"; return; }
  local out
  out=$("$py" -c "
import sys
sys.path.insert(0, '$cp')
from modules import claude_usage
u = claude_usage.fetch_usage()
print('' if u['session_pct'] is None else round(u['session_pct']))
print('' if u['weekly_pct'] is None else round(u['weekly_pct']))
print(u['reason'] or '')
" 2>/dev/null) || { LIMIT_NOTE="fetch failed (python error)"; return; }
  local sp wp reason
  sp=$(printf '%s\n' "$out" | sed -n 1p)
  wp=$(printf '%s\n' "$out" | sed -n 2p)
  reason=$(printf '%s\n' "$out" | sed -n 3p)
  [[ -n "$sp" ]] && SESS_LIMIT="${sp}%"
  [[ -n "$wp" ]] && WEEK_LIMIT="${wp}%"
  case "$reason" in
    ok)          LIMIT_NOTE="from claude.ai/api/oauth/usage (ok)" ;;
    auth_error)  LIMIT_NOTE="auth_error — logged out / token rejected (use Claude, then retry)" ;;
    fetch_error) LIMIT_NOTE="fetch_error — couldn't reach claude.ai" ;;
    *)           LIMIT_NOTE="reason: ${reason:-unknown}" ;;
  esac
}
[[ "$FETCH" -eq 1 ]] && fetch_limits

# ----- output -----
nightlbl() { [[ "$1" == "true" ]] && echo " 🌙" || echo ""; }
echo "resman window status — $(date -d "@$NOW" '+%Y-%m-%d %H:%M:%S (%A)')"
echo "config: $SRC"
echo "windows: ${STARTS[*]}  length: ${LENGTH}h"
echo

if [[ "$cur_found" -eq 1 ]]; then
  WPCT=$(pct $((NOW - cur_start)) $((cur_end - cur_start)))
  printf "Window %d/%d  %s–%s%s  %s time %3s%%  limit %4s   (ends in %s)\n" \
    "$cur_i" "$COUNT" "$(clk "$cur_start")" "$(clk "$cur_end")" "$(nightlbl "$cur_night")" \
    "$(bar "$WPCT")" "$WPCT" "$SESS_LIMIT" "$(hm $((cur_end - NOW)))"
else
  if [[ "$next_start" -ge 0 ]]; then
    printf "Window —/%d  (between windows)              %s time   —%%  limit %4s   (next %s%s in %s)\n" \
      "$COUNT" "$(bar 0)" "$SESS_LIMIT" "$(clk "$next_start")" "$(nightlbl "$next_night")" "$(hm $((next_start - NOW)))"
  else
    echo "Window: no windows configured"
  fi
fi

printf "Week        anchor %s %02d:00%*s  %s time %3s%%  limit %4s   (resets in %s)\n" \
  "${WEEKDAY_NAMES[$WD]}" "$HR" 6 "" \
  "$(bar "$WEEK_PCT")" "$WEEK_PCT" "$WEEK_LIMIT" "$(hm "$WEEK_LEFT")"

echo
echo "limit: session = 5-hour window, weekly = 7-day — $LIMIT_NOTE"
echo "time:  share of the window / week elapsed (the footer bar fill + inside %)."
