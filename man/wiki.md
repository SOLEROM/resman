# Wiki

The **Wiki** tab renders markdown pages produced by the Claude wiki plugin
inside each vault. The convention is:

```
<vault>/wiki/overview.md   ← landing page (loaded by default)
<vault>/wiki/hot.md        ← the "what's hot right now" page
<vault>/wiki/index.md      ← table-of-contents page
<vault>/wiki/<topic>.md    ← topic pages
```

resman opens `wiki/overview.md` for the currently selected vault when the tab
is shown. The toolbar exposes search, random, a read toggle, a favorite
toggle, the favorites list, three explicit page buttons, plus a reload:

- **Search wiki…** — type a query and press Enter to search every page
  (titles weigh more than body text). Clearing the box restores the page.
- **Random** — jump to a random **unread** page (see Read/unread below).
- **Mark read ✓ / Mark unread** — toggle the open page's read state.
- **☆ Favorite / ★ Favorited** — add or remove the open page in the vault's
  favorites file (see Favorites below).
- **★ Favorites** — open the favorites list: every entry is a link.
- **Hot** — loads `wiki/hot.md`
- **Index** — loads `wiki/index.md`
- **Overview** — loads `wiki/overview.md`
- **↻** — reloads the current page (also reloads the tree)

## Sidebar page tree

The Wiki tab has a **left sidebar** listing every markdown page under
`<vault>/wiki/` (recursively). Click a page to load it — the selected page is
highlighted with an accent box and scrolled into view. Subdirectories nest
under their parent. Click the sidebar's **↻** to refresh the tree.

The first row of the tree is always **★ Favorites** (with a count of favorite
pages) — click it to open the vault's favorites list (see Favorites below).

## Read / unread

resman tracks which pages you've read. **Unread** pages show an accent dot in
the tree; folders containing unread pages show a faint dot. Read state is
stored as tiny sidecar marker files next to each page (`.<page>.unrd`), so it
survives the rsync that mirrors wiki pages between machines.

- Opening a page does **not** mark it read — use **Mark read ✓** to do that
  explicitly (and **Mark unread** to flip it back).
- New or freshly-synced pages show up as unread automatically.
- **Random** picks a random unread page so you can chip away at the backlog.

## Favorites

Each vault keeps its favorite pages in **`<vault>/.favorites.md`** — a plain
markdown file at the vault root (next to `wiki/` and `.obsidian/`), one page
per line. Favorite pages show a small **★** at the right of their row in the
sidebar tree.

- **☆ Favorite** appends the open page; **★ Favorited** removes it again.
  resman writes entries as Obsidian wikilinks with the page title as alias —
  `- [[wiki/concepts/gguf.md|GGUF]]` — so the file also works as a link list
  when opened inside Obsidian.
- **★ Favorites** renders the list in the content pane. Click an entry to open
  the page (Back returns to the list); the **✕** on a row removes it. An entry
  whose page no longer exists is struck through and tagged **missing** so you
  can spot and prune it.
- **You can edit the file by hand.** Paths are relative to the vault root
  (`wiki/concepts/gguf.md`); a missing `.md` is assumed. Any of these lines is
  a favorite, with or without a list bullet:

  ```
  wiki/concepts/gguf.md
  - [[wiki/hot|Hot page]]
  - [Index](wiki/index.md)
  ```

  Headings, blank lines, `<!-- comments -->` and prose are left alone —
  removing a favorite from the UI drops only that entry's line and keeps the
  rest of the file byte-for-byte. Favoriting a page that doesn't exist on disk
  is refused, as is a page whose name contains `[`, `]`, `|` or `#` (Obsidian
  can't link those either).

## API

The browser fetches these endpoints:

```
GET  /api/vaults/<name>/wiki/tree            ← sidebar tree (each file has `unread`, `favorite`)
GET  /api/vaults/<name>/wiki?file=…          ← a single page's markdown
POST /api/vaults/<name>/wiki/read            ← { file, read } toggle read/unread
GET  /api/vaults/<name>/wiki/random          ← a random unread page
GET  /api/vaults/<name>/wiki/favorites       ← { file, exists, favorites: [{file, title, exists}] }
POST /api/vaults/<name>/wiki/favorites       ← { file, favorite } add/remove in .favorites.md
GET  /api/vaults/<name>/wiki/search?q=…      ← ranked search hits
```

Path traversal is blocked server-side — the resolved file must live under the
vault directory. The tree endpoint returns `{"missing": true, "tree": []}` if
the vault has no `wiki/` directory yet.

## When there is no wiki yet

If `wiki/overview.md` doesn't exist, the panel shows:

> No wiki page found at `wiki/overview.md` yet. Open a Claude session for
> this vault and run the wiki plugin to generate one.

That's the cue to:

1. Click **+ Claude** in the Terminal tab for that vault.
2. Run the wiki plugin (`/claude-obsidian:wiki` or whichever slash command
   your plugin exposes).
3. Reload the Wiki tab.

## Editing wiki pages

The Wiki tab is **read-only**. Edit pages either inside Obsidian (click
the **Obsidian** button in the header) or by editing the file on disk
directly.

## Linking between pages

**Obsidian-style `[[wikilinks]]` are clickable.** They render as dashed
underlined links inside the wiki page; clicking one loads the target page in
the Wiki tab. Both syntaxes are supported:

- `[[Page Name]]` — link text is the page name, target resolves to
  `wiki/Page Name.md` (or a near match in the tree if the file lives in a
  subdir or uses a different case).
- `[[Page Name|alias]]` — link text is `alias`, target resolves the same way.
- `![[Page Name]]` — embed syntax collapses to a regular link in v1.

If the target doesn't exist on disk, the content pane shows the standard
"not found" error and the sidebar tree stays as-is — useful for spotting
broken links.

## Where the field "wiki home" comes from

The vault **health modal** has a row called "Wiki home found" — that just
checks whether `wiki/overview.md` exists on disk. It's the same convention as
the tab's default page.
