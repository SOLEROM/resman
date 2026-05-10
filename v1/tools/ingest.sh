#!/usr/bin/env bash
# Vault-agnostic URL → ingest helper.
# Usage: ingest.sh <vault_path> <url>
set -euo pipefail

if [[ $# -lt 2 ]]; then
  echo "usage: $0 <vault_path> <url>" >&2
  exit 64
fi

VAULT="$1"
URL="$2"

if [[ ! -d "$VAULT" ]]; then
  echo "error: vault path not found: $VAULT" >&2
  exit 66
fi

cd "$VAULT"
echo "[ingest] vault=$VAULT url=$URL"

# Delegate to claude-obsidian if available; otherwise log the URL into the
# vault as a simple inbox note so the operator can verify the wiring.
if command -v claude >/dev/null 2>&1; then
  claude -p "/claude-obsidian:wiki-ingest $URL" --dangerously-skip-permissions
else
  mkdir -p "$VAULT/inbox"
  printf -- "- %s\n" "$URL" >> "$VAULT/inbox/queued-urls.md"
  echo "[ingest] claude not on PATH — appended URL to inbox/queued-urls.md"
fi
