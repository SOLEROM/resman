#!/usr/bin/env bash
# urlIn.sh — ingest a URL into the Obsidian wiki vault
#
# Usage:
#   ./urlIn.sh <url>
#   ./urlIn.sh <url> --can      # also update main.canvas
#
# Requires: claude CLI (Claude Code) in PATH.
# Add to ~/bin/ and chmod +x for system-wide use.

set -euo pipefail

VAULT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TODAY=$(date +%Y-%m-%d)
URL=""
DO_CANVAS=false

# ── Helpers ───────────────────────────────────────────────────────────────────

usage() {
  cat <<'EOF'

  urlIn.sh — fetch a URL and ingest it into the Obsidian wiki vault

  Usage:
    urlIn.sh <url>          Ingest URL into wiki
    urlIn.sh <url> --can    Ingest + update wiki/canvases/main.canvas
    urlIn.sh --help         Show this message

  Examples:
    urlIn.sh https://techcrunch.com/some-article
    urlIn.sh https://example.com/report --can

EOF
  exit "${1:-0}"
}

die() { echo "" >&2; echo "✗  Error: $*" >&2; echo "" >&2; exit 1; }

# ── Arg parse ─────────────────────────────────────────────────────────────────

[[ $# -eq 0 ]] && usage 1

for arg in "$@"; do
  case "$arg" in
    --can)      DO_CANVAS=true ;;
    --help|-h)  usage 0 ;;
    http*)      URL="$arg" ;;
    *)          die "unrecognised argument '$arg'" ;;
  esac
done

[[ -z "$URL" ]] && die "no URL provided — pass a full http/https URL as the first argument"

# ── Canvas block (injected into prompt only when --can is set) ────────────────

CANVAS_BLOCK=""
if [[ "$DO_CANVAS" == "true" ]]; then
  CANVAS_BLOCK=$(cat <<'CANVAS_EOF'

CANVAS UPDATE (--can flag was passed — run this after all wiki pages are written):
1. Read wiki/canvases/main.canvas (JSON canvas format).
2. Compute max_y = max(node.y + node.height) across ALL existing nodes.
3. Check if any existing group zone already matches the ingested topic.
   - If yes: add new file nodes inside that zone.
   - If no: create a new group zone at y = max_y + 60:
       id = "zone-[topic-slug]-[unix-timestamp]"
       label = short topic name, color = "2" (orange)
       x = -980, width = 2000, height = 280
4. For each new content wiki page created (NOT source pages under wiki/sources/):
   - Add a file node: type="file", vault-relative path, width=320, height=100
   - id = "file-[slug]-[unix-timestamp]"
   - Position inside the zone left-to-right, 40px gap between nodes:
       First node: (zone.x + 20, zone.y + 20)
       Next nodes: x += 360; wrap to new row if x + 320 > zone.x + zone.width - 20
5. Write the updated canvas JSON back to wiki/canvases/main.canvas.
Report: zone used/created and number of nodes added to canvas.
CANVAS_EOF
  )
fi

# ── Main prompt ───────────────────────────────────────────────────────────────

PROMPT=$(cat <<PROMPT_EOF
You are a wiki ingest agent. Vault root: $VAULT. Today: $TODAY.

TASK: Ingest this URL into the wiki: $URL

Follow these steps in order — do not skip any:

STEP 1 — FETCH
Use WebFetch on the URL. If the page is auth-walled, empty, or returns an error,
print a clear explanation and stop.

STEP 2 — TRANSLATE
If the content is not in English, translate everything to English before creating
any wiki pages. Note the original language in the raw file frontmatter.

STEP 3 — SAVE RAW
Save to: .raw/articles/[url-slug]-$TODAY.md
Frontmatter: source_url, fetched: $TODAY, original_language (only if translated).

STEP 4 — CHECK MANIFEST
Read .raw/.manifest.json.
If this URL already has an entry with the same hash, print:
  "Already ingested — skipping."
and stop.

STEP 5 — CHOOSE FOLDER
Pick the best wiki subfolder based on the content:
  wiki/domain/   → new speciality domain or topic area (default)
  wiki/market/   → market sizing, competitive landscape, funding data
  wiki/ideas/    → startup ideas, product concepts, gap analysis
  wiki/entities/ → company or person profiles
  wiki/concepts/ → frameworks, methodologies, definitions
Use judgment. Check wiki/index.md to see what already exists.

STEP 6 — CREATE PAGES
  a. wiki/sources/[slug].md — source summary page
     Frontmatter: type: source, source_url, date_published, confidence (high/medium/low), key_claims list
     Body: 2–4 sentence summary; what this source contributes

  b. 2–4 content pages in the chosen folder
     Each page covers one distinct topic, entity, or concept from the source
     Cross-link pages to each other with [[wikilinks]]

  c. wiki/entities/ pages for significant companies or people mentioned
     Only create if they don't already exist (check wiki/index.md first)

STEP 7 — UPDATE META (all three required)
  a. wiki/index.md
     Add new pages under the correct section heading. Add new section headings if needed.

  b. wiki/log.md
     Prepend a new entry at the TOP of the log (above all existing entries):
       ## $TODAY — ingest | [Source Title]
       - Source: .raw/articles/[filename].md
       - Pages created: [[page1]], [[page2]], ...
       - Key insight: one sentence summary of what is new

  c. wiki/hot.md
     Update the "Last Session" section: topic, key numbers/facts, pages created.
     Move the current Last Session to a "Previous Session" block below it.
$CANVAS_BLOCK
STEP 8 — UPDATE MANIFEST
Append to .raw/.manifest.json under "sources":
{
  "hash": "fetched-$TODAY",
  "ingested_at": "$TODAY",
  "source_url": "$URL",
  "pages_created": [...vault-relative paths...],
  "pages_updated": ["wiki/index.md", "wiki/log.md", "wiki/hot.md"]
}

FINAL REPORT
After all files are written, print this block exactly:

╔══════════════════════════════════════════════════════╗
  Ingested : $URL
  Topic    : [one-line description of what was ingested]
  Language : [source language, or "English"]
  Folder   : wiki/[chosen subfolder]/
  Pages created:
    • [vault-relative path]
    • [vault-relative path]
    ...
  Canvas   : [updated — N nodes added / not updated]
╚══════════════════════════════════════════════════════╝
PROMPT_EOF
)

# ── Execute ───────────────────────────────────────────────────────────────────

echo ""
echo "  Vault  : $VAULT"
echo "  URL    : $URL"
echo "  Canvas : $( [[ "$DO_CANVAS" == "true" ]] && echo "will update" || echo "skip" )"
echo ""
echo "  Fetching and ingesting…"
echo ""

cd "$VAULT"

# --dangerouslySkipPermissions allows unattended tool use (Read/Write/WebFetch/Bash).
# Safe here because this script runs on your own machine against your own vault.
if ! claude --dangerously-skip-permissions -p "$PROMPT"; then
  echo "" >&2
  die "claude exited with a non-zero status — see output above"
fi

echo ""
echo "✓  Reference ingested — vault: $VAULT"
echo ""
