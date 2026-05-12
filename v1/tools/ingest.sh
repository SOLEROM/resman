#!/usr/bin/env bash
# Vault-agnostic URL → ingest helper.
# Usage: ingest.sh <vault_path> <url> [--prefix <file>] [--can]
#
#   --prefix <file>   prepend the contents of <file> to the claude prompt
#                     (e.g. constructive-extraction guidance for sources
#                     that discuss harmful applications)
#   --can             after ingesting, also update wiki/canvases/main.canvas
#                     with the newly created/updated pages
set -euo pipefail

if [[ $# -lt 2 ]]; then
  echo "usage: $0 <vault_path> <url> [--prefix <file>] [--can]" >&2
  exit 64
fi

VAULT="$1"
URL="$2"
shift 2

PREFIX_FILE=""
UPDATE_CANVAS=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prefix)
      if [[ $# -lt 2 ]]; then
        echo "error: --prefix requires a file path" >&2
        exit 64
      fi
      PREFIX_FILE="$2"
      shift 2
      ;;
    --can)
      UPDATE_CANVAS=1
      shift
      ;;
    # Legacy positional: a bare third arg was treated as <prefix_file>. Keep
    # accepting it so older callers that haven't been updated still work.
    *)
      if [[ -z "$PREFIX_FILE" && "$1" != --* ]]; then
        PREFIX_FILE="$1"
        shift
      else
        echo "error: unknown argument: $1" >&2
        exit 64
      fi
      ;;
  esac
done

if [[ ! -d "$VAULT" ]]; then
  echo "error: vault path not found: $VAULT" >&2
  exit 66
fi

PREFIX_TEXT=""
if [[ -n "$PREFIX_FILE" ]]; then
  if [[ ! -f "$PREFIX_FILE" ]]; then
    echo "error: prefix file not found: $PREFIX_FILE" >&2
    exit 66
  fi
  PREFIX_TEXT="$(cat "$PREFIX_FILE")"
fi

cd "$VAULT"
echo "[ingest] vault=$VAULT url=$URL prefix=${PREFIX_FILE:-none} canvas=$UPDATE_CANVAS"

CANVAS_INSTRUCTION=""
if [[ "$UPDATE_CANVAS" -eq 1 ]]; then
  # Asks the agent to refresh wiki/canvases/main.canvas with the new content
  # right after the wiki-ingest finishes. Mirrors the `--can` behaviour of
  # the in-vault injest.sh from wikValTemplate.
  CANVAS_INSTRUCTION=$'\n\nAfter the ingest completes, also update wiki/canvases/main.canvas to include the newly created or updated wiki pages — add a card per page, group related cards into zones, and draw edges to existing related notes. Use /claude-obsidian:canvas or /canvas add note as appropriate.'
fi

# Delegate to claude-obsidian if available; otherwise log the URL into the
# vault as a simple inbox note so the operator can verify the wiring.
if command -v claude >/dev/null 2>&1; then
  if [[ -n "$PREFIX_TEXT" ]]; then
    PROMPT=$(printf "%s\n\n---\n\n/claude-obsidian:wiki-ingest %s%s\n" "$PREFIX_TEXT" "$URL" "$CANVAS_INSTRUCTION")
  else
    PROMPT=$(printf "/claude-obsidian:wiki-ingest %s%s\n" "$URL" "$CANVAS_INSTRUCTION")
  fi
  claude -p "$PROMPT" --dangerously-skip-permissions
else
  mkdir -p "$VAULT/inbox"
  ENTRY="- $URL"
  [[ -n "$PREFIX_FILE" ]] && ENTRY+="  (prefix: $PREFIX_FILE)"
  [[ "$UPDATE_CANVAS" -eq 1 ]] && ENTRY+="  (update-canvas)"
  printf "%s\n" "$ENTRY" >> "$VAULT/inbox/queued-urls.md"
  echo "[ingest] claude not on PATH — appended URL to inbox/queued-urls.md"
fi
