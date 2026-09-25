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
toggle, three explicit page buttons, plus a reload (the favorites *list* is
the pinned first row of the page tree, not a toolbar button):

- **Search wiki…** — type a query and press Enter to search every page
  (titles weigh more than body text). Clearing the box restores the page.
- **Random** — jump to a random **unread** page (see Read/unread below).
- **Mark read ✓ / Mark unread** — toggle the open page's read state.
- **☆ Favorite / ★ Favorited** — add or remove the open page in the vault's
  favorites file (see Favorites below).
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
- **★ Favorites**, the pinned first row of the page tree, renders the list in
  the content pane. Click an entry to open
  the page (Back returns to the list); the **✕** on a row removes it. An entry
  whose page no longer exists is struck through and tagged **missing** so you
  can spot and prune it.
- **When a toggle is refused** (the page vanished from disk, the vault root is
  not writable, an unsafe path) the reason appears in a red notice right under
  the toolbar and the button keeps its state. It is never a browser dialog:
  docked in mainBench, resman runs in an iframe where dialogs are dropped
  silently. The server logs the refusal too (`journalctl --user -u resman`).
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

## Highlights

Select text on a wiki page and a small palette appears: six colors — yellow,
green, blue, pink, orange, purple. One click highlights the selection. The
highlight is **written into the page's markdown** as

```
<mark class="hl-yellow">the highlighted words</mark>
```

so it travels with the vault: Obsidian, the other bench apps and any markdown
viewer show it (the file names the color, the theme picks the shade).

- **Click a highlight** to change its color or remove it (**✕**).
- **Ctrl+click** (or Alt+click) a sentence to select the whole sentence.
- **Pen mode** (the pencil in the palette): every selection is highlighted at
  once with the last color. A **Pen** chip at the bottom right switches the
  color and turns it off.
- Keyboard, while the palette is open: **1–6** pick a color, **0** / Delete
  removes, **Esc** closes.
- A selection over several paragraphs or list items becomes one highlight per
  block. Selecting part of a wikilink, a `code span` or a **bold** run takes
  the whole of it. Highlighting over an existing highlight paints over it.
- Not highlightable: code blocks, the folded **metadata** (frontmatter and the
  prose before the first heading), pages outside `wiki/`, search results, the
  favorites list and the Help tab.
- resman only ever adds, removes or recolors these tags — the rest of the file
  stays byte-for-byte. If the page changed on disk while you were reading (an
  agent, Obsidian, an rsync), nothing is written: the page reloads and you
  select again.
- Highlighting does **not** change the page's read/unread state; other pages
  that changed meanwhile still show up as unread.
- Search and page titles ignore the tags.
- Agents rewrite pages: tell them to keep the tags. Add this line to the
  vault's `CLAUDE.md` conventions:
  `- <mark class="hl-…">…</mark> is the reader's highlight. Keep the tags around the same words when you edit a note; never add your own.`

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
POST /api/vaults/<name>/wiki/highlight       ← { file, base_sha, op: add|remove|recolor, … }
```

The page GET also returns `sha` (the highlight route's concurrency token:
a stale one is a 409) and `highlightable`. `dry_run: true` on the highlight
POST returns the resulting page without writing it.

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

The Wiki tab does not edit page text — only reader highlights (above). Edit
pages either inside Obsidian (click the **Obsidian** button in the header) or
by editing the file on disk directly.

## Safety

Wiki pages are untrusted input (written by Claude sessions, synced from other
machines): every rendered page — wiki and Help — goes through DOMPurify, so
scripts, event handlers, iframes, forms and styles in a page are dropped. The
highlight route never takes markup from the browser: it builds the tag from a
fixed color list and refuses any edit that would change the page text.

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
