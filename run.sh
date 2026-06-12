#!/usr/bin/env bash
# Convenience launcher for resman.
#
# Usage:
#   ./run.sh                          # default venv (.venv); auto-creates if absent
#   ./run.sh --vname /path/to/venv    # use a specific venv (absolute or relative
#                                     # to project root). Pair with the same
#                                     # --vname value passed to deps.sh.
#   ./run.sh --public                 # bind to 0.0.0.0 — accessible on the LAN.
#                                     # (Disables CORS origin restriction; ttyd
#                                     # terminals are reachable from LAN too.
#                                     # Resman has no auth — only run --public
#                                     # on a trusted network.)
# Any remaining args are forwarded to control-plane/server.py
# (e.g. --port 5099, --host 0.0.0.0, --no-scheduler).

set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

VNAME=""
USER_PROVIDED_VENV=0
FORWARD=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --vname)
      [[ $# -ge 2 ]] || { echo "--vname requires a path"; exit 64; }
      VNAME="$2"; USER_PROVIDED_VENV=1; shift 2 ;;
    --vname=*)
      VNAME="${1#*=}"; USER_PROVIDED_VENV=1; shift ;;
    -h|--help)
      sed -n '2,11p' "$0"; exit 0 ;;
    *)
      FORWARD+=("$1"); shift ;;
  esac
done

if [[ -z "$VNAME" ]]; then
  VENV="$ROOT/.venv"
elif [[ "$VNAME" = /* || "$VNAME" = ~* ]]; then
  VENV="${VNAME/#\~/$HOME}"
else
  VENV="$ROOT/$VNAME"
fi

venv_works() {
  [[ -x "$VENV/bin/python3" ]] && "$VENV/bin/python3" -c "import sys" >/dev/null 2>&1
}

if ! venv_works; then
  if [[ "$USER_PROVIDED_VENV" -eq 1 ]] && [[ -d "$VENV" ]]; then
    # User-provided path that doesn't work — bail out rather than nuke it.
    echo "venv at $VENV is broken (python3 missing or fails to run)."
    echo "Fix or recreate it, or pass a different --vname. Refusing to delete user-provided path."
    exit 4
  fi
  if [[ -d "$VENV" ]]; then
    echo "Stale venv detected at $VENV — recreating."
    rm -rf "$VENV"
  fi
  echo "Creating venv at $VENV ..."
  python3 -m venv "$VENV"
  "$VENV/bin/python3" -m pip install --quiet --upgrade pip
  "$VENV/bin/python3" -m pip install --quiet -r control-plane/requirements.txt
fi

if [[ ! -f "$HOME/.resman.yaml" && ! -f config/resman.yaml ]]; then
  echo "No resman.yaml found."
  echo "  Place one at ~/.resman.yaml (per-user override), OR"
  echo "  Copy config/resman.yaml.example to config/resman.yaml and edit."
  exit 1
fi

exec "$VENV/bin/python" control-plane/server.py "${FORWARD[@]}"
