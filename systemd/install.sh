#!/usr/bin/env bash
# Installs resman as a systemd user service on Ubuntu/Debian hosts.
# Run as the user who will own the service (not root).
#
# Usage:
#   ./install.sh                          # auto-detects project root (one level up)
#   ./install.sh --project /path/to/repo  # explicit project root
#   ./install.sh --vname .vevn22          # venv name passed to run.sh (default: .venv)
#   ./install.sh --no-start               # install + enable only, don't start now
#   ./install.sh --uninstall              # stop, disable, and remove the service

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_PATH="$(cd -- "$SCRIPT_DIR/.." && pwd)"
VENV_NAME=".venv"
START_NOW=1
UNINSTALL=0
SERVICE_NAME="resman"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project)   PROJECT_PATH="$2"; shift 2 ;;
    --project=*) PROJECT_PATH="${1#*=}"; shift ;;
    --vname)     VENV_NAME="$2"; shift 2 ;;
    --vname=*)   VENV_NAME="${1#*=}"; shift ;;
    --no-start)  START_NOW=0; shift ;;
    --uninstall) UNINSTALL=1; shift ;;
    -h|--help)
      sed -n '2,9p' "$0"; exit 0 ;;
    *) echo "Unknown argument: $1"; exit 64 ;;
  esac
done

SERVICE_DIR="$HOME/.config/systemd/user"
SERVICE_FILE="$SERVICE_DIR/$SERVICE_NAME.service"
TEMPLATE="$SCRIPT_DIR/resman.service.template"

# ── Uninstall ────────────────────────────────────────────────────────────────
if [[ "$UNINSTALL" -eq 1 ]]; then
  echo "Uninstalling resman service..."
  systemctl --user stop "$SERVICE_NAME" 2>/dev/null || true
  systemctl --user disable "$SERVICE_NAME" 2>/dev/null || true
  rm -f "$SERVICE_FILE"
  systemctl --user daemon-reload
  echo "Done. Service removed."
  exit 0
fi

# ── Preflight checks ─────────────────────────────────────────────────────────
if [[ ! -f "$TEMPLATE" ]]; then
  echo "ERROR: service template not found at $TEMPLATE"
  exit 1
fi

if [[ ! -x "$PROJECT_PATH/v1/run.sh" ]]; then
  echo "ERROR: run.sh not found or not executable at $PROJECT_PATH/v1/run.sh"
  echo "  Pass the correct project root with --project /path/to/repo"
  exit 1
fi

if ! systemctl --user status >/dev/null 2>&1; then
  echo "ERROR: systemd user session is not available."
  echo "  Make sure you are running as a normal user (not root) with a D-Bus session."
  exit 1
fi

# ── Install ──────────────────────────────────────────────────────────────────
echo "Installing resman systemd user service"
echo "  project : $PROJECT_PATH"
echo "  venv    : $VENV_NAME"
echo "  service : $SERVICE_FILE"
echo

mkdir -p "$SERVICE_DIR"

sed \
  -e "s|__PROJECT_PATH__|${PROJECT_PATH}|g" \
  -e "s|__VENV_NAME__|${VENV_NAME}|g" \
  "$TEMPLATE" > "$SERVICE_FILE"

systemctl --user daemon-reload
systemctl --user enable "$SERVICE_NAME"

# Enable linger so the service survives logout and starts at boot.
if loginctl enable-linger "$(whoami)" 2>/dev/null; then
  echo "  linger  : enabled (service will start at boot without login)"
else
  echo "  linger  : could not enable (may need: sudo loginctl enable-linger $(whoami))"
fi

if [[ "$START_NOW" -eq 1 ]]; then
  systemctl --user start "$SERVICE_NAME"
  echo
  systemctl --user status "$SERVICE_NAME" --no-pager || true
else
  echo
  echo "Service installed and enabled. Start it with:"
  echo "  systemctl --user start $SERVICE_NAME"
fi

echo
echo "Useful commands:"
echo "  systemctl --user status  $SERVICE_NAME"
echo "  systemctl --user start   $SERVICE_NAME"
echo "  systemctl --user stop    $SERVICE_NAME"
echo "  systemctl --user restart $SERVICE_NAME"
echo "  journalctl --user -u $SERVICE_NAME -f"
