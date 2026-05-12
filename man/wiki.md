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
is shown. The toolbar exposes three explicit page buttons plus a reload:

- **Hot** — loads `wiki/hot.md`
- **Index** — loads `wiki/index.md`
- **Overview** — loads `wiki/overview.md`
- **↻** — reloads the current page (also reloads the tree)

## Sidebar page tree

The Wiki tab has a **left sidebar** listing every markdown page under
`<vault>/wiki/` (recursively). Click a page to load it. The active page is
highlighted. Subdirectories nest under their parent. Click the sidebar's
**↻** to refresh the tree without reloading the current page.

## API

The browser fetches two endpoints:

```
GET /api/vaults/<name>/wiki/tree            ← the sidebar tree
GET /api/vaults/<name>/wiki?file=…          ← a single page's markdown
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
