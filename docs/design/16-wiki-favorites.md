# Wiki Favorites

## Overview

`modules/wiki_favorites.py` lets the user pin wiki pages per vault. The store is
a single **hand-editable markdown file at the vault root**:

```
<vault>/.favorites.md
```

It is owned jointly by the user (edited in any editor, or inside Obsidian) and
by resman (the ☆/★ toggle in the Wiki tab), so the module reads leniently and
writes conservatively. There is no cache and no sidecar state — the file is the
truth on every request, which keeps it rsync-safe like the read/unread markers.

## File format

One favorite per line, vault-relative (`wiki/concepts/gguf.md`). Accepted forms,
each optionally behind a `-`/`*`/`+` bullet, a `1.` number, or a `[ ]` checkbox:

| Line | Parsed as |
|------|-----------|
| `wiki/concepts/gguf.md` | `wiki/concepts/gguf.md` |
| `[[wiki/hot\|Hot page]]`, `[[wiki/hot#Section]]`, `![[wiki/hot]]` | `wiki/hot.md` |
| `[Index](wiki/index.md)`, `[Log](<wiki/log.md>)`, `[x](wiki/Page%20Name.md)` | `wiki/index.md`, `wiki/log.md`, `wiki/Page Name.md` |

Rules applied by `normalize()`: trim, drop a leading `./`, collapse `//`, append
`.md` when the last segment has no extension. Rejected (ignored on read, 400 on
write): empty, absolute, any `..` segment, control characters, and any of
`[ ] | #` — those terminate the wikilink line the module writes, so accepting
them would create an entry that can never be parsed back (Obsidian forbids them
in note names anyway). Headings,
`<!-- -->` / `%% %%` comments, blank lines and prose are skipped — a bare line
counts as a path only if it contains a `/` or ends in `.md`. Duplicates collapse
to their first occurrence.

resman writes entries as `- [[<path>|<page title>]]` (title = first `# H1`
after any frontmatter, else the file stem; `|`, `[[`, `]]` stripped from the
alias) so the file doubles as a working link list inside Obsidian. A new file
starts with `# Favorites`.

## Operations

- **`list_favorites(vault_root)`** — ordered, de-duplicated paths.
- **`entries(vault_root)`** — `[{file, title, exists}]`; `exists` is false for
  a dangling entry so the UI can flag it instead of hiding it.
- **`add(vault_root, rel)`** — no-op (returns False) when already listed;
  otherwise appends one line. Never rewrites existing lines.
- **`remove(vault_root, rel)`** — drops every line that *resolves* to `rel`
  (so a hand-written `[GGUF](wiki/concepts/gguf.md)` is matched by the UI's
  `wiki/concepts/gguf.md`) and keeps everything else byte-for-byte.
- Writes go through a temp file + `os.replace` in the vault root (atomic;
  a failed write leaves no temp file behind). The existing file's permission
  bits are preserved; a new file is 0644 like the unread markers.
- `add`/`remove` hold a per-vault `threading.Lock` around their
  read-modify-write so two concurrent toggles (two tabs, a double click) can't
  drop each other's line. Flask serves requests concurrently, so this is the
  normal case, not a corner one.
- The file is read and written with `newline=""`, so a CRLF file stays CRLF
  and an appended line uses the file's own line ending.
- The title lookup for the alias goes through `page_path()` (resolve +
  containment check), so a symlink inside the vault can't leak an outside
  file's heading into `.favorites.md`; the alias has `[`, `]`, `|` stripped.

## API

```
GET  /api/vaults/<name>/wiki/favorites   ← { file: ".favorites.md", exists, favorites: [{file, title, exists}] }
POST /api/vaults/<name>/wiki/favorites   ← { file, favorite: bool } → { file, favorite, favorites }
GET  /api/vaults/<name>/wiki/tree        ← file nodes now carry `favorite: bool`
```

`POST` requires CSRF and is idempotent. Favoriting a page that doesn't exist on
disk (after resolving symlinks inside the vault) is refused with 404; removing
always succeeds so dangling entries can be cleaned. The tree endpoint reads the
file best-effort — a broken favorites file never breaks the tree.

## Frontend

- `state.wikiFavorites` (a `Set` of vault-relative paths) is rebuilt from the
  tree response and replaced wholesale by every favorites API reply.
- **☆ Favorite / ★ Favorited** (`#btn-wiki-fav`) sits next to Mark read. It is
  hidden on the favorites view itself and after a page 404s.
- **★ Favorites** loads the pseudo-page `.favorites.md`: `loadWiki()` renders
  the list (`renderWikiFavoritesView`) instead of fetching markdown, so the
  list takes part in Back/Forward history and the ↻ reload like any page.
  Rows link via `data-wiki-file` (exact path, no wikilink resolution) and carry
  a ✕ that removes the entry in place; missing pages are struck through.
- Favorite pages show a right-aligned ★ in the sidebar tree (absolutely
  positioned so label truncation can't hide it). The codicon subset is
  untouched — the star is a text glyph.
- The tree's first row is a pinned **★ Favorites** entry (`.wiki-pinned-fav`,
  with a count badge) rendered as a regular `.wiki-file` so it shares the
  label styling, the active highlight and the click binding; its `data-path`
  is `.favorites.md`, so clicking it is `loadWiki(WIKI_FAVORITES)`. The row is
  `position: sticky` inside the scrolling `.docs-tree`, so it stays on top
  even after the tree auto-scrolls to the open page.
