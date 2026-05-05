## Claude Plugin Commands

The `claude-obsidian` plugin is installed. These slash commands work directly in the Claude Code chat and operate on this vault. Use them instead of manual file editing whenever possible — they handle linking, logging, and cross-referencing automatically.

### `/claude-obsidian:wiki-ingest` — Add anything to the wiki

The main command for adding external content. Pass a URL, a file path, or just describe what to add.

```
/claude-obsidian:wiki-ingest https://techcrunch.com/article-about-competitor
/claude-obsidian:wiki-ingest .raw/clipped-article.md
/claude-obsidian:wiki-ingest https://competitor.com — add to market area
```

What it does: fetches the source, strips noise, extracts entities and concepts, creates or updates the right wiki note, adds cross-references, and logs to `wiki/log.md`. Far better than Method 1 or 2 manual drops for anything substantive.

**Startup use cases:**
- Clip a competitor's pricing page → auto-filed to `wiki/market/`
- Paste a founder's blog post → extracts key ideas and links to relevant notes
- Drop a job listing from a competitor → signals their product direction

### `/claude-obsidian:wiki-query` — Ask questions about what's in the vault

Use this instead of manually searching notes. Ask in natural language.

```
/claude-obsidian:wiki-query what do we know about crypto payroll competitors?
/claude-obsidian:wiki-query which ideas have no validation yet?
/claude-obsidian:wiki-query who should I talk to about the expense tracking idea?
```

Good answers get filed back into the wiki automatically so they're findable later.

### `/claude-obsidian:autoresearch` — Deep research on a topic

Runs an autonomous loop: searches the web, fetches sources, synthesizes findings, and files everything into the wiki as structured pages. Use this when you want to map a whole space, not just one article.

```
/claude-obsidian:autoresearch crypto payroll and contractor tax tooling landscape 2025
/claude-obsidian:autoresearch competitors to Deel for freelancer financial tools
/claude-obsidian:autoresearch market size for B2B expense management software
```

This will run several searches, cross-reference findings, and produce multiple linked wiki pages. Good for when you're starting to research a new idea area from scratch.

### `/claude-obsidian:save` — Save a conversation insight into the vault

After a productive chat (brainstorm, analysis, feedback session), run this to preserve the useful parts as a structured wiki note.

```
/claude-obsidian:save
/claude-obsidian:save — save as a new idea note
/claude-obsidian:save — this was a market analysis, file accordingly
```

Use it after: customer interview debrief, competitive analysis chat, brainstorm session. Don't let good conversation context disappear.

### `/claude-obsidian:defuddle` — Clean a web page before ingesting

Strips ads, nav bars, cookie banners, and boilerplate from a web page, leaving clean markdown. Run this before ingesting a noisy page.

```
/claude-obsidian:defuddle https://messy-news-site.com/article
```

Then pipe the clean output into `/wiki-ingest`. Saves 40-60% of token noise on typical news or blog pages. Especially useful for competitor websites that have heavy marketing copy.

### `/claude-obsidian:wiki-lint` — Health check the vault

Finds broken links, orphan notes (no connections), missing frontmatter, and empty sections.

```
/claude-obsidian:wiki-lint
```

Run this once a week or after a big ingest session. An orphan note is a note that isn't thinking — it has no links and contributes nothing to the map. Lint finds them so you can connect or kill them.

### `/claude-obsidian:canvas` — Build a visual map

Creates an Obsidian canvas (visual board) from wiki notes. Useful for seeing the whole idea space at once or preparing for a pitch.

```
/claude-obsidian:canvas create a map of all ideas and their market connections
/claude-obsidian:canvas show all competitors and which ideas they validate or threaten
```

### `/claude-obsidian:wiki-fold` — Compress the log

After many ingests, `wiki/log.md` gets long. This command rolls up older entries into a summary meta-page, keeping the log lean.

```
/claude-obsidian:wiki-fold
```

Run this when `wiki/log.md` gets unwieldy (50+ entries).

---

## Plugin Command Cheat Sheet

| What you want to do      | Command                                  |
| ------------------------ | ---------------------------------------- |
| Add a URL or article     | `/claude-obsidian:wiki-ingest <url>`     |
| Research a whole topic   | `/claude-obsidian:autoresearch <topic>`  |
| Ask what's in the vault  | `/claude-obsidian:wiki-query <question>` |
| Save this conversation   | `/claude-obsidian:save`                  |
| Clean a noisy web page   | `/claude-obsidian:defuddle <url>`        |
| Check vault health       | `/claude-obsidian:wiki-lint`             |
| Make a visual map        | `/claude-obsidian:canvas <description>`  |
| Compress old log entries | `/claude-obsidian:wiki-fold`             |
