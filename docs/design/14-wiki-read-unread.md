# Wiki Read/Unread, Search & Random

## Overview

`modules/wiki_unread.py` tracks which wiki pages have been read, and powers wiki
search and "random unread" navigation. The model is ported from the garage
resman wiki: it's marker-file based so it survives the rsync that mirrors the
same wiki pages between the garage resman and this project.

## Read/unread model

State is tracked with **sidecar marker files** colocated with each page:

```
wiki/concepts/gguf.md      ← the page
wiki/concepts/.gguf.unrd    ← exists ⟺ the page is UNREAD
wiki/.unrd-scan             ← baseline; its mtime = last reconcile time
```

- **Reading a page does NOT auto-mark it read** (matches garage). The user
  toggles state explicitly with the **Mark read ✓ / Mark unread** button.
- **`reconcile(wiki_dir)`** runs on every tree load:
  - first scan (no baseline) marks every page unread;
  - later scans mark any page whose **ctime** is newer than the baseline as
    unread — so freshly rsync'd pages surface as unread — and prune markers for
    deleted pages;
  - the baseline mtime is bumped to the scan start.
- Marker creation is atomic (`O_CREAT|O_EXCL`); `mark_read` unlinks (idempotent).
- State is **global to the vault**, not per-user.

Path traversal is rejected (no `..`, no leading `/`, must resolve under the wiki
directory).

## Search

`search(wiki_dir, query)` ranks pages: **titles weigh 5×, body 1×**, all query
tokens must appear (AND), results sorted by score then path, capped at 50 (and a
2000-file scan cap). Each hit carries `{ file, rel, title, snippet, score }`; the
snippet is server-built HTML with query tokens wrapped in `<mark>` (the text is
escaped first, so it's safe to inject).

## Random

`pick_random_unread(wiki_dir)` reconciles, then returns a random unread page (or
`null` when everything is read).

## API

```
GET  /api/vaults/<name>/wiki/tree            ← each file node carries `unread: bool`
POST /api/vaults/<name>/wiki/read            ← { file, read } → { file, unread }
GET  /api/vaults/<name>/wiki/random          ← { file: "wiki/…" | null }
GET  /api/vaults/<name>/wiki/search?q=…      ← { query, hits: [...] }
```

The tree endpoint reconciles before responding (best-effort; a reconcile failure
never breaks the tree). `POST …/read` requires CSRF.

## Frontend

- **Tree** — unread files show a leading accent dot and bold label; folders with
  any unread descendant show a faint dot. The selected page is highlighted with
  an accent rail (selection box) and scrolled into view on jump.
- **Toolbar** — a **Search wiki…** box (Enter to search, clear to restore the
  page), a **Random** button (jumps to a random unread page), and a
  **Mark read / Mark unread** toggle for the open page.

## Key decisions

- **Marker files, not a database** — survives rsync and is inspectable on disk;
  ctime-vs-baseline is what flags rsync'd pages as unread.
- **No auto-mark on open** — mirrors garage; reading is an explicit action so the
  unread set stays meaningful.
- **Reconcile on tree load** — keeps the sidebar honest without a separate
  background job; it's idempotent and best-effort.
