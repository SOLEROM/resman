#!/usr/bin/env bash
# remoteAgent.sh — CLI bridge to the running resman control plane.
#
# Two callers:
#   1. Human at terminal — interactive vault picker + operation menu.
#   2. Remote agent (e.g. openClaw on phone, over SSH) — non-interactive
#      with everything as flags, --json output, clear exit codes.
#
# Tasks created here go through POST /api/tasks so they appear in the
# Tasks tab automatically (same code path as the UI). The wiki-ingest
# operation is dispatched by the control plane to v1/tools/ingest.sh —
# do NOT call that script directly from here; we want validation,
# window-gating, log streaming, and tab visibility for free.
#
# Exit codes:
#   0  success
#   1  usage / argument error
#   2  server unreachable
#   3  server returned an error (4xx / 5xx)
#   4  --wait: task ended in failed / cancelled / interrupted state
#   5  --wait: poll timed out
#
# Requires: bash, curl, python3.

set -euo pipefail

# ─── Paths ────────────────────────────────────────────────────────────────────

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
USER_OVERRIDE="$HOME/.resman.yaml"
REPO_RESMAN_YAML="$ROOT_DIR/v1/config/resman.yaml"

# ─── Defaults ─────────────────────────────────────────────────────────────────

OP=""
VAULT=""
URL=""
TOPIC=""
DESCRIPTION=""
UPDATE_CANVAS=false
PRIORITY="high"
BASE_URL="${RESMAN_BASE_URL:-}"
WAIT=false
TIMEOUT=600
JSON=false
QUIET=false
ACTION=""        # list-vaults | list-tasks | run | (empty → interactive)
FORCE=true       # bypass window gate by default — phone-driven users can't open the window

# Operations exposed here. Deliberately omits run-shell / run-prompt so a
# remote agent over SSH cannot turn this into arbitrary command execution.
ALLOWED_OPS=(
  "wiki-ingest"
  "wiki-ingest-prefix"
  "wiki-canvas"
  "wiki-lint"
  "wiki-autoresearch"
  "wiki-update-hot-cache"
  "wiki-bootstrap"
)

# ─── Helpers ──────────────────────────────────────────────────────────────────

usage() {
  cat <<'EOF'
remoteAgent.sh — drive the resman control plane from a CLI (or SSH from openClaw).

Actions:
  (none)               Interactive vault picker + operation menu (requires TTY).
  --list-vaults        Print configured vault names, one per line.
  --list-tasks [N]     Print the most recent N tasks (default 10) as compact text.
  --vault NAME --op OP [op args]   Create a task non-interactively.

Operations:
  --op wiki-ingest          --url URL [--update-canvas]
  --op wiki-ingest-prefix   --url URL [--update-canvas]
  --op wiki-canvas          [--description TEXT]
  --op wiki-lint
  --op wiki-autoresearch    --topic TEXT
  --op wiki-update-hot-cache
  --op wiki-bootstrap

Common flags:
  --priority {high|medium|low}   Default: high
  --no-force                     Honor window-gate (default is to bypass it,
                                 since a phone-driven user can't open it).
  --wait                         Poll until terminal state.
  --timeout SECONDS              Wait timeout (default: 600).
  --base-url URL                 Resman base URL (default: read from resman.yaml).
  --json                         Single JSON line on stdout (for openClaw).
  --quiet                        Errors only.
  -h, --help                     This message.

Examples:
  ./tools/remoteAgent.sh
  ./tools/remoteAgent.sh --list-vaults
  ./tools/remoteAgent.sh --vault alpha --op wiki-ingest --url https://x.com/a --update-canvas
  ssh host '/path/to/remoteAgent.sh --vault alpha --op wiki-lint --wait --json'
EOF
}

die()  { echo "✗ $*" >&2; exit "${EXIT:-1}"; }
log()  { $QUIET || echo "$*"; }
logn() { $QUIET || printf "%s" "$*"; }

contains() {
  local needle="$1"; shift
  local item
  for item in "$@"; do [[ "$item" == "$needle" ]] && return 0; done
  return 1
}

# Read a scalar field from resman.yaml. Returns "" if not found.
# Path can be "app.host" → reads `app:` then `host:` underneath.
yaml_field() {
  local path="$1" file="$2"
  python3 - "$path" "$file" <<'PY'
import sys, yaml
path, file = sys.argv[1], sys.argv[2]
try:
    with open(file, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
except FileNotFoundError:
    print(""); sys.exit(0)
except Exception:
    print(""); sys.exit(0)
cur = data
for part in path.split("."):
    if not isinstance(cur, dict) or part not in cur:
        print(""); sys.exit(0)
    cur = cur[part]
print(cur if cur is not None else "")
PY
}

# Pick the live resman.yaml using the same priority order ConfigManager uses:
# ~/.resman.yaml first, then v1/config/resman.yaml.
pick_resman_yaml() {
  if [[ -f "$USER_OVERRIDE" ]]; then
    echo "$USER_OVERRIDE"
  elif [[ -f "$REPO_RESMAN_YAML" ]]; then
    echo "$REPO_RESMAN_YAML"
  else
    echo ""
  fi
}

# Decide which base URL to talk to. Order:
#   1. --base-url flag (already in $BASE_URL if provided)
#   2. $RESMAN_BASE_URL env
#   3. http://<app.host>:<app.port> from resman.yaml
#   4. http://127.0.0.1:5090
resolve_base_url() {
  [[ -n "$BASE_URL" ]] && return
  local file; file="$(pick_resman_yaml)"
  local host="" port=""
  if [[ -n "$file" ]]; then
    host="$(yaml_field app.host "$file")"
    port="$(yaml_field app.port "$file")"
  fi
  [[ -z "$host" ]] && host="127.0.0.1"
  [[ -z "$port" ]] && port="5090"
  BASE_URL="http://${host}:${port}"
}

# curl wrapper. Distinguishes three failure modes:
#   - connection failure / DNS / timeout → exit 2 ("server unreachable")
#   - HTTP 4xx / 5xx → exit 3 with the body included in the error message
#   - 2xx → echo the body to stdout for the caller to consume.
# Sets the CSRF header on every request (POST handlers reject without it).
api() {
  local method="$1" path="$2" body="${3:-}"
  local body_file="/tmp/.remoteAgent.body.$$"
  local err_file="/tmp/.remoteAgent.err.$$"
  local status
  local -a curl_args=(
    -sS -m 15
    -o "$body_file"
    -w "%{http_code}"
    -X "$method"
    -H "X-Requested-With: resman"
    -H "Accept: application/json"
  )
  if [[ -n "$body" ]]; then
    curl_args+=(-H "Content-Type: application/json" --data "$body")
  fi
  # Capture status. curl only returns non-zero on transport failures here
  # (since we didn't pass --fail / --fail-with-body); HTTP 4xx/5xx still
  # exit 0 with the status code in %{http_code}.
  if ! status=$(curl "${curl_args[@]}" "${BASE_URL}${path}" 2>"$err_file"); then
    local err; err="$(cat "$err_file" 2>/dev/null || true)"
    rm -f "$body_file" "$err_file"
    EXIT=2 die "cannot reach resman at ${BASE_URL} — ${err:-connection failed}"
  fi
  local out; out="$(cat "$body_file" 2>/dev/null || true)"
  rm -f "$body_file" "$err_file"
  if [[ -z "$status" ]] || ! [[ "$status" =~ ^[0-9]+$ ]]; then
    EXIT=2 die "cannot reach resman at ${BASE_URL} — no HTTP status"
  fi
  if (( status >= 400 )); then
    EXIT=3 die "server returned HTTP ${status}: ${out}"
  fi
  printf "%s" "$out"
}

# Build a JSON body from key=value pairs. Values are passed through as JSON
# (so strings need to be already quoted). Uses python so we don't reinvent
# escaping in bash.
build_json() {
  python3 - "$@" <<'PY'
import json, sys
out = {}
for kv in sys.argv[1:]:
    k, _, v = kv.partition("=")
    out[k] = json.loads(v)
print(json.dumps(out))
PY
}

json_str() {
  # Quote a string for embedding into JSON. python3 -c is overkill but bulletproof.
  python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$1"
}

json_get() {
  # Extract a top-level field from a JSON string. Prints "" if missing.
  local payload="$1" key="$2"
  python3 - "$payload" "$key" <<'PY'
import json, sys
try:
    data = json.loads(sys.argv[1])
except Exception:
    sys.exit(0)
print(data.get(sys.argv[2], "") if isinstance(data, dict) else "")
PY
}

# ─── List actions ─────────────────────────────────────────────────────────────

vault_names_via_api() {
  api GET /api/vaults | python3 -c '
import json, sys
data = json.load(sys.stdin)
for v in data.get("vaults", []):
    if v.get("registered") is not False:
        print(v.get("name", ""))
' | grep -v '^$' || true
}

vault_names_via_yaml() {
  local file; file="$(pick_resman_yaml)"
  [[ -z "$file" ]] && return 1
  python3 - "$file" <<'PY'
import sys, yaml
with open(sys.argv[1], "r", encoding="utf-8") as f:
    data = yaml.safe_load(f) or {}
for v in (data.get("vaults") or []):
    name = v.get("name") if isinstance(v, dict) else None
    if name:
        print(name)
PY
}

do_list_vaults() {
  local names=""
  if names=$(vault_names_via_api 2>/dev/null) && [[ -n "$names" ]]; then
    printf "%s\n" "$names"
    return
  fi
  # Fall back to YAML so this works even when the server is down.
  if names=$(vault_names_via_yaml 2>/dev/null); then
    printf "%s\n" "$names"
    return
  fi
  EXIT=2 die "no vaults found — neither the API nor resman.yaml returned any"
}

do_list_tasks() {
  local limit="${1:-10}"
  # Pull the JSON body into a variable so the python heredoc doesn't
  # collide with a pipe on stdin. The body + limit are passed as argv.
  local body; body="$(api GET "/api/tasks?limit=${limit}")"
  python3 - "$body" "$limit" <<'PY'
import json, sys
data = json.loads(sys.argv[1])
limit = int(sys.argv[2])
tasks = data.get("tasks", [])[:limit]
if not tasks:
    print("(no tasks)")
    sys.exit(0)
for t in tasks:
    print("{:<14} {:<11} {:<22} {:<14} {}".format(
        t.get("id", "?"),
        t.get("state", "?"),
        t.get("operation", "?"),
        t.get("vault", "?"),
        t.get("created_at", ""),
    ))
PY
}

# ─── Interactive pickers ──────────────────────────────────────────────────────

require_tty() {
  if [[ ! -t 0 ]]; then
    die "interactive mode requires a TTY — pass --vault/--op flags instead"
  fi
}

pick_vault_interactive() {
  require_tty
  local names; names="$(do_list_vaults)"
  [[ -z "$names" ]] && die "no configured vaults"
  log ""
  log "Vaults:"
  local i=1
  local -a arr=()
  while IFS= read -r line; do
    log "  $i. $line"
    arr+=("$line")
    ((i++))
  done <<< "$names"
  log ""
  printf "Select vault [1-%d]: " "${#arr[@]}" >&2
  local choice; read -r choice
  if ! [[ "$choice" =~ ^[0-9]+$ ]] || (( choice < 1 || choice > ${#arr[@]} )); then
    die "invalid choice"
  fi
  VAULT="${arr[$((choice-1))]}"
  log "Selected: $VAULT"
}

pick_op_interactive() {
  require_tty
  log ""
  log "Operations:"
  log "  1. wiki-ingest         — ingest a URL"
  log "  2. wiki-ingest-prefix  — ingest a URL with constructive-extraction prefix"
  log "  3. wiki-canvas         — update the visual canvas"
  log "  4. wiki-lint           — run vault health check"
  log "  5. wiki-autoresearch   — deep research on a topic"
  log "  6. wiki-update-hot-cache — refresh hot cache"
  log "  7. wiki-bootstrap      — re-run wiki bootstrap"
  log ""
  printf "Select operation [1-7]: " >&2
  local choice; read -r choice
  case "$choice" in
    1) OP="wiki-ingest" ;;
    2) OP="wiki-ingest-prefix" ;;
    3) OP="wiki-canvas" ;;
    4) OP="wiki-lint" ;;
    5) OP="wiki-autoresearch" ;;
    6) OP="wiki-update-hot-cache" ;;
    7) OP="wiki-bootstrap" ;;
    *) die "invalid choice" ;;
  esac
}

prompt_op_params_interactive() {
  case "$OP" in
    wiki-ingest|wiki-ingest-prefix)
      printf "URL: " >&2; read -r URL
      [[ -z "$URL" ]] && die "URL required"
      printf "Update canvas after ingest? [y/N]: " >&2
      local ans; read -r ans
      [[ "$ans" =~ ^[Yy] ]] && UPDATE_CANVAS=true
      ;;
    wiki-canvas)
      printf "Description (blank = plugin defaults): " >&2; read -r DESCRIPTION
      ;;
    wiki-autoresearch)
      printf "Topic: " >&2; read -r TOPIC
      [[ -z "$TOPIC" ]] && die "topic required"
      ;;
  esac
}

# ─── Validate non-interactive flags ───────────────────────────────────────────

validate_run_args() {
  [[ -z "$VAULT" ]] && die "--vault required"
  [[ -z "$OP" ]] && die "--op required"
  if ! contains "$OP" "${ALLOWED_OPS[@]}"; then
    die "operation '$OP' is not exposed by remoteAgent (allowed: ${ALLOWED_OPS[*]})"
  fi
  case "$OP" in
    wiki-ingest|wiki-ingest-prefix)
      [[ -z "$URL" ]] && die "--url required for $OP"
      [[ ! "$URL" =~ ^https?:// ]] && die "--url must be http(s)://…"
      ;;
    wiki-autoresearch)
      [[ -z "$TOPIC" ]] && die "--topic required for wiki-autoresearch"
      ;;
  esac
  if ! contains "$PRIORITY" high medium low; then
    die "--priority must be high|medium|low"
  fi
}

# ─── Task creation ────────────────────────────────────────────────────────────

build_params_json() {
  case "$OP" in
    wiki-ingest|wiki-ingest-prefix)
      python3 -c 'import json,sys; print(json.dumps({"url": sys.argv[1], "update_canvas": sys.argv[2] == "true"}))' "$URL" "$UPDATE_CANVAS"
      ;;
    wiki-canvas)
      python3 -c 'import json,sys; print(json.dumps({"description": sys.argv[1]}))' "$DESCRIPTION"
      ;;
    wiki-autoresearch)
      python3 -c 'import json,sys; print(json.dumps({"topic": sys.argv[1]}))' "$TOPIC"
      ;;
    *)
      printf '{}'
      ;;
  esac
}

submit_task() {
  local params; params="$(build_params_json)"
  local body
  body=$(python3 - "$VAULT" "$OP" "$params" "$PRIORITY" "$FORCE" <<'PY'
import json, sys
vault, op, params_json, priority, force = sys.argv[1:6]
body = {
    "name": f"remote-{op}",
    "vault": vault,
    "operation": op,
    "params": json.loads(params_json),
    "priority": priority,
    "force": force.lower() == "true",
}
print(json.dumps(body))
PY
)
  api POST /api/tasks "$body"
}

# Poll GET /api/tasks/<id> until the task hits a terminal state or we time out.
# Prints progress dots on a TTY; quiet on a pipe.
wait_for_task() {
  local tid="$1" deadline interval=2 state=""
  deadline=$(( $(date +%s) + TIMEOUT ))
  local terminal=("completed" "failed" "cancelled" "interrupted" "archived")
  while :; do
    local task
    task="$(api GET "/api/tasks/${tid}")"
    state="$(json_get "$task" state)"
    if contains "$state" "${terminal[@]}"; then
      echo "$task"
      return 0
    fi
    if (( $(date +%s) >= deadline )); then
      EXIT=5 die "timed out after ${TIMEOUT}s waiting for $tid (last state: $state)"
    fi
    [[ -t 1 ]] && printf "."
    sleep "$interval"
  done
}

# ─── Output ───────────────────────────────────────────────────────────────────

emit_create_result() {
  local task="$1"
  local tid state
  tid="$(json_get "$task" id)"
  state="$(json_get "$task" state)"
  if $JSON; then
    python3 - "$task" "$BASE_URL" <<'PY'
import json, sys
t = json.loads(sys.argv[1])
out = {
    "task_id": t.get("id"),
    "state": t.get("state"),
    "vault": t.get("vault"),
    "operation": t.get("operation"),
    "url": sys.argv[2],
}
print(json.dumps(out))
PY
  else
    log "Task created: $tid"
    log "State:        $state"
    log "Tasks tab:    ${BASE_URL}/  (watch progress here)"
  fi
}

emit_wait_result() {
  local task="$1"
  local tid state rc
  tid="$(json_get "$task" id)"
  state="$(json_get "$task" state)"
  rc="$(json_get "$task" exit_code)"
  if $JSON; then
    echo "$task"
  else
    log ""
    log "Task $tid → $state (exit ${rc:-?})"
  fi
  if [[ "$state" == "completed" ]]; then
    return 0
  fi
  EXIT=4 die "task ended in non-success state: $state"
}

# ─── Arg parsing ──────────────────────────────────────────────────────────────

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)       usage; exit 0 ;;
    --list-vaults)   ACTION="list-vaults"; shift ;;
    --list-tasks)
      ACTION="list-tasks"
      LIMIT="10"
      if [[ "${2:-}" =~ ^[0-9]+$ ]]; then LIMIT="$2"; shift; fi
      shift ;;
    --vault)         VAULT="$2"; shift 2 ;;
    --op)            OP="$2"; shift 2 ;;
    --url)           URL="$2"; shift 2 ;;
    --topic)         TOPIC="$2"; shift 2 ;;
    --description)   DESCRIPTION="$2"; shift 2 ;;
    --update-canvas) UPDATE_CANVAS=true; shift ;;
    --priority)      PRIORITY="$2"; shift 2 ;;
    --no-force)      FORCE=false; shift ;;
    --wait)          WAIT=true; shift ;;
    --timeout)       TIMEOUT="$2"; shift 2 ;;
    --base-url)      BASE_URL="$2"; shift 2 ;;
    --json)          JSON=true; QUIET=true; shift ;;
    --quiet)         QUIET=true; shift ;;
    *) die "unknown argument: $1 (try --help)" ;;
  esac
done

resolve_base_url

# ─── Dispatch ─────────────────────────────────────────────────────────────────

case "$ACTION" in
  list-vaults)
    do_list_vaults
    ;;
  list-tasks)
    do_list_tasks "${LIMIT:-10}"
    ;;
  *)
    # Run-task path. Pick vault/op interactively only when no flags were given
    # AND we have a TTY; otherwise validate the flags and fail clearly.
    if [[ -z "$VAULT" && -z "$OP" && -t 0 ]]; then
      log "resman remote agent"
      log "==================="
      pick_vault_interactive
      pick_op_interactive
      prompt_op_params_interactive
    fi
    validate_run_args
    task="$(submit_task)"
    emit_create_result "$task"
    if $WAIT; then
      tid="$(json_get "$task" id)"
      [[ -z "$tid" ]] && die "couldn't extract task id from server response"
      final="$(wait_for_task "$tid")"
      emit_wait_result "$final"
    fi
    ;;
esac
