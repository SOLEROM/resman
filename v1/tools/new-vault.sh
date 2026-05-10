#!/usr/bin/env bash
# Scaffold a fresh research vault.
# Usage: new-vault.sh <name> <target_path>
set -euo pipefail

if [[ $# -lt 2 ]]; then
  echo "usage: $0 <name> <target_path>" >&2
  exit 64
fi

NAME="$1"
TARGET="$2"

if [[ -e "$TARGET" ]]; then
  echo "error: target path already exists: $TARGET" >&2
  exit 65
fi

mkdir -p "$TARGET"
mkdir -p "$TARGET/.obsidian"
mkdir -p "$TARGET/inbox"
mkdir -p "$TARGET/_resman"

cat > "$TARGET/README.md" <<EOF
# ${NAME}

A new research vault scaffolded by resman.

Run \`/wiki\` in a Claude Code session inside this vault to scaffold
the wiki structure.
EOF

# Always exclude _resman/ from git tracking — ObsidianPush rewrites it every 60s.
if [[ ! -f "$TARGET/.gitignore" ]]; then
  printf "_resman/\n" > "$TARGET/.gitignore"
elif ! grep -q "^_resman/" "$TARGET/.gitignore"; then
  printf "\n_resman/\n" >> "$TARGET/.gitignore"
fi

echo "[new-vault] created $TARGET"
echo "[new-vault] register it in resman by adding to system.yaml or via the UI"
