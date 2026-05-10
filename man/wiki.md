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
- **↻** — reloads the current page

## API

The browser fetches:

```
GET /api/vaults/<name>/wiki?file=wiki/overview.md
```

Path traversal is blocked server-side — the resolved file must live under the
vault directory.

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
**Open Obsidian** in the Terminal tab) or by editing the file on disk
directly.

## Linking between pages

Internal markdown links inside a wiki page are not yet rewritten. If your
wiki uses Obsidian-style `[[wikilinks]]` they will render as plain text.
Standard markdown links to relative `.md` files work via the URL bar but the
SPA does not yet provide a navigation overlay — open the file directly via
Obsidian or the terminal.

## Where the field "wiki home" comes from

The vault **health modal** has a row called "Wiki home found" — that just
checks whether `wiki/overview.md` exists on disk. It's the same convention as
the tab's default page.
