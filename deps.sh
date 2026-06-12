#!/usr/bin/env bash
# resman dependency installer.
#
# Ensures every host dependency for resman v1 is present:
#   - tmux                  (mandatory; server refuses to start without it)
#   - python3 + venv        (mandatory)
#   - ttyd                  (optional; browser terminals are disabled without it)
#   - Python packages       (Flask, Flask-SocketIO, eventlet, PyYAML, APScheduler, pytest)
#
# Idempotent — safe to re-run. Detects apt (Debian/Ubuntu), dnf (Fedora/RHEL),
# pacman (Arch), and brew (macOS). Falls back to a clear error otherwise.
#
# Usage:
#   ./deps.sh                          # install everything (default venv: .venv)
#   ./deps.sh --no-sudo                # skip sudo system installs; only set up venv
#   ./deps.sh --check                  # report only; install nothing
#   ./deps.sh --vname /path/to/venv    # use a specific venv path (absolute or
#                                      # relative to project root). Useful when
#                                      # you maintain venvs per python version
#                                      # (Ubuntu 20: 3.8, Ubuntu 24: 3.12, etc.).
#                                      # Pass the SAME path to run.sh.

set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

MODE="install"
USE_SUDO=1
VNAME=""                # full path to venv; empty = default .venv
USER_PROVIDED_VENV=0    # 1 if user passed --vname (then we never auto-recreate)
while [[ $# -gt 0 ]]; do
  case "$1" in
    --check)    MODE="check"; shift ;;
    --no-sudo)  USE_SUDO=0; shift ;;
    --vname)
      [[ $# -ge 2 ]] || { echo "--vname requires a path"; exit 64; }
      VNAME="$2"; USER_PROVIDED_VENV=1; shift 2 ;;
    --vname=*)  VNAME="${1#*=}"; USER_PROVIDED_VENV=1; shift ;;
    -h|--help)  sed -n '2,24p' "$0"; exit 0 ;;
    *)          echo "unknown arg: $1"; exit 64 ;;
  esac
done

# Resolve venv path: empty → default .venv; relative → relative to ROOT.
if [[ -z "$VNAME" ]]; then
  VENV="$ROOT/.venv"
elif [[ "$VNAME" = /* || "$VNAME" = ~* ]]; then
  # Already absolute (or starts with ~ which the shell expanded).
  VENV="${VNAME/#\~/$HOME}"
else
  VENV="$ROOT/$VNAME"
fi

# ----- helpers -----
c_red()    { printf "\033[31m%s\033[0m" "$1"; }
c_green()  { printf "\033[32m%s\033[0m" "$1"; }
c_yellow() { printf "\033[33m%s\033[0m" "$1"; }
c_dim()    { printf "\033[2m%s\033[0m" "$1"; }

ok()      { printf "  [%s] %s\n" "$(c_green ok)"   "$1"; }
miss()    { printf "  [%s] %s — %s\n" "$(c_red miss)" "$1" "$2"; }
warn()    { printf "  [%s] %s — %s\n" "$(c_yellow warn)" "$1" "$2"; }
info()    { printf "  %s\n" "$(c_dim "$1")"; }

have() { command -v "$1" >/dev/null 2>&1; }

# Detect host architecture for prebuilt binary downloads.
arch_for_release() {
  case "$(uname -m)" in
    x86_64|amd64) echo "x86_64" ;;
    aarch64|arm64) echo "aarch64" ;;
    armv7l) echo "armhf" ;;
    i386|i686) echo "i686" ;;
    *) echo "" ;;
  esac
}

run_priv() {
  if [[ "$USE_SUDO" -eq 1 ]]; then
    if [[ "$EUID" -eq 0 ]]; then
      "$@"
    else
      sudo "$@"
    fi
  else
    echo "  (skipped — --no-sudo): $*"
    return 1
  fi
}

# ----- detect package manager -----
PM=""
if have apt-get; then PM="apt"
elif have dnf; then PM="dnf"
elif have pacman; then PM="pacman"
elif have brew; then PM="brew"
fi

# Map our logical names to OS package names.
pkg_for() {
  local what="$1"
  case "$PM:$what" in
    apt:tmux)        echo "tmux" ;;
    apt:python)      echo "python3 python3-venv python3-pip" ;;
    apt:ttyd)        echo "ttyd" ;;
    apt:build)       echo "build-essential cmake libjson-c-dev libwebsockets-dev" ;;

    dnf:tmux)        echo "tmux" ;;
    dnf:python)      echo "python3 python3-pip" ;;
    dnf:ttyd)        echo "ttyd" ;;
    dnf:build)       echo "@development-tools cmake json-c-devel libwebsockets-devel" ;;

    pacman:tmux)     echo "tmux" ;;
    pacman:python)   echo "python python-pip" ;;
    pacman:ttyd)     echo "ttyd" ;;
    pacman:build)    echo "base-devel cmake json-c libwebsockets" ;;

    brew:tmux)       echo "tmux" ;;
    brew:python)     echo "python@3.12" ;;
    brew:ttyd)       echo "ttyd" ;;
    brew:build)      echo "cmake json-c libwebsockets" ;;

    *) echo "" ;;
  esac
}

pkg_install() {
  local what="$1"
  local pkgs
  pkgs="$(pkg_for "$what")"
  if [[ -z "$pkgs" ]]; then
    warn "$what" "no package mapping for package manager '$PM'"
    return 1
  fi
  case "$PM" in
    apt)    run_priv env DEBIAN_FRONTEND=noninteractive apt-get update -y \
              && run_priv env DEBIAN_FRONTEND=noninteractive apt-get install -y $pkgs ;;
    dnf)    run_priv dnf install -y $pkgs ;;
    pacman) run_priv pacman -Sy --noconfirm --needed $pkgs ;;
    brew)   brew install $pkgs ;;
  esac
}

# Try to install ttyd when the package manager can't:
#   1. snap (if snapd is available)
#   2. prebuilt static binary from GitHub releases (x86_64 / aarch64 / armhf / i686)
# Returns 0 on success, 1 on failure. Never aborts the script.
install_ttyd_fallback() {
  if have snap; then
    echo "  trying: snap install ttyd --classic"
    if run_priv snap install ttyd --classic 2>/dev/null; then
      ok "ttyd installed via snap"
      return 0
    fi
  fi
  local arch
  arch="$(arch_for_release)"
  if [[ -z "$arch" ]]; then
    return 1
  fi
  if ! have curl; then
    warn "curl" "needed for prebuilt-binary fallback; install curl first"
    return 1
  fi
  local version="1.7.7"
  local url="https://github.com/tsl0922/ttyd/releases/download/${version}/ttyd.${arch}"
  local dest="/usr/local/bin/ttyd"
  echo "  trying: download prebuilt ttyd ${version} (${arch}) → ${dest}"
  local tmp
  tmp="$(mktemp)"
  if curl -fsSL --connect-timeout 10 -o "$tmp" "$url"; then
    if run_priv install -m 0755 "$tmp" "$dest"; then
      rm -f "$tmp"
      ok "ttyd installed at $dest"
      return 0
    fi
  fi
  rm -f "$tmp"
  return 1
}

# Detect a venv whose interpreter shebang points at a path that no longer
# exists. This happens when the project directory was moved/copied, since
# venv shebangs are absolute paths to the original .venv/bin/python3.
venv_is_broken() {
  local venv="$1"
  [[ -d "$venv" ]] || return 0   # absent counts as "broken" so we recreate
  local py="$venv/bin/python3"
  if [[ ! -x "$py" ]]; then
    return 0
  fi
  # Reading the shebang of pip is the most reliable check — its #! must
  # resolve to a real interpreter on this host.
  local pip="$venv/bin/pip"
  if [[ -f "$pip" ]]; then
    local shebang
    shebang="$(head -n1 "$pip" 2>/dev/null | sed 's/^#!//')"
    local interp
    interp="$(echo "$shebang" | awk '{print $1}')"
    if [[ -n "$interp" && ! -x "$interp" ]]; then
      return 0
    fi
  fi
  if ! "$py" -c "import sys" >/dev/null 2>&1; then
    return 0
  fi
  return 1
}

# ----- step 1: report current state -----
echo
echo "resman dependency check"
echo "  package manager: ${PM:-<none detected>}"
echo

PROBLEMS=0
declare -a INSTALL_QUEUE=()

check_or_queue() {
  local label="$1"; shift
  local cmd="$1"; shift
  local what="$1"; shift
  local optional="${1:-no}"
  if have "$cmd"; then
    ok "$label ($("$cmd" --version 2>&1 | head -n1 || echo present))"
  else
    if [[ "$optional" == "yes" ]]; then
      warn "$label" "missing — optional"
    else
      miss "$label" "missing"
      PROBLEMS=$((PROBLEMS + 1))
    fi
    INSTALL_QUEUE+=("$what:$optional")
  fi
}

check_or_queue "tmux"     tmux    tmux    no
check_or_queue "python3"  python3 python  no
check_or_queue "ttyd"     ttyd    ttyd    yes

# Check python venv module without invoking it
if have python3; then
  if python3 -c "import venv" 2>/dev/null; then
    ok "python3-venv (module available)"
  else
    miss "python3-venv" "python venv module not available"
    PROBLEMS=$((PROBLEMS + 1))
    INSTALL_QUEUE+=("python:no")
  fi
fi

echo

if [[ "$MODE" == "check" ]]; then
  if [[ "$PROBLEMS" -gt 0 ]]; then
    echo "$(c_red "✗") $PROBLEMS required dependency/dependencies missing."
    exit 1
  fi
  echo "$(c_green "✓") All required dependencies present."
  exit 0
fi

# ----- step 2: install missing system packages -----
if [[ ${#INSTALL_QUEUE[@]} -gt 0 ]]; then
  if [[ -z "$PM" ]]; then
    echo "$(c_red "✗") No supported package manager detected (apt, dnf, pacman, brew)."
    echo "  Install manually: tmux, python3-venv, and (optionally) ttyd."
    exit 2
  fi
  echo "Installing missing system packages via '$PM'..."
  # Deduplicate queue by 'what'
  declare -A SEEN
  for entry in "${INSTALL_QUEUE[@]}"; do
    what="${entry%%:*}"
    optional="${entry##*:}"
    if [[ -n "${SEEN[$what]:-}" ]]; then
      continue
    fi
    SEEN[$what]=1
    if [[ "$what" == "ttyd" ]]; then
      # apt does not always have ttyd in older releases; try fallbacks.
      if ! pkg_install "$what" 2>/dev/null; then
        warn "ttyd" "not in package manager — trying snap / prebuilt binary"
        install_ttyd_fallback || warn "ttyd" "all fallbacks failed; install manually: https://github.com/tsl0922/ttyd"
      fi
    else
      pkg_install "$what" || {
        if [[ "$optional" == "no" ]]; then
          echo "$(c_red "✗") Failed to install required package: $what"
          exit 3
        fi
      }
    fi
  done
  echo
fi

# ----- step 3: python venv + packages -----
echo "Using venv at: $VENV"

if [[ ! -d "$VENV" ]]; then
  echo "Creating Python venv at $VENV ..."
  python3 -m venv "$VENV"
elif [[ "$USER_PROVIDED_VENV" -eq 0 ]] && venv_is_broken "$VENV"; then
  # Auto-repair only applies to the default .venv (e.g. when a project tree
  # was copied across hosts and shebangs no longer resolve). For
  # user-provided --vname paths we never destroy the directory.
  echo "Detected stale .venv (shebangs point to a path that no longer exists)."
  echo "Removing and recreating ..."
  rm -rf "$VENV"
  python3 -m venv "$VENV"
fi

PY="$VENV/bin/python3"
PIP="$PY -m pip"

if [[ ! -x "$PY" ]]; then
  echo "$(c_red "✗") venv is broken — python3 not found at $PY"
  echo "  If you provided --vname, make sure the path is correct or"
  echo "  let deps.sh create it (the directory must not already exist as"
  echo "  a non-venv folder)."
  exit 4
fi

echo "Upgrading pip ..."
$PIP install --quiet --upgrade pip

echo "Installing Python deps from control-plane/requirements.txt ..."
$PIP install --quiet -r "$ROOT/control-plane/requirements.txt"

# ----- step 4: verification -----
echo
echo "Verifying Python imports ..."
if "$PY" -c "import flask, flask_socketio, eventlet, yaml, apscheduler" 2>/dev/null; then
  ok "Python packages importable"
else
  echo "$(c_red "✗") Python packages failed to import:"
  "$PY" -c "import flask, flask_socketio, eventlet, yaml, apscheduler" || true
  exit 5
fi

# Final summary
echo
echo "$(c_green "✓") resman dependencies are ready."
echo
echo "Next steps:"
RUN_FLAG=""
if [[ "$USER_PROVIDED_VENV" -eq 1 ]]; then
  RUN_FLAG=" --vname \"$VNAME\""
fi
if [[ ! -f "$HOME/.resman.yaml" && ! -f "$ROOT/config/resman.yaml" ]]; then
  echo "  1. cp config/resman.yaml.example config/resman.yaml  # then edit it"
  echo "     (or place a per-user override at ~/.resman.yaml)"
  echo "  2. ./run.sh${RUN_FLAG}"
else
  echo "  ./run.sh${RUN_FLAG}"
fi
echo
if ! have ttyd; then
  echo "$(c_yellow "note") ttyd is missing — browser terminal sessions will be disabled."
  echo "       The rest of resman works normally. To enable, install ttyd:"
  echo "         https://github.com/tsl0922/ttyd"
fi
