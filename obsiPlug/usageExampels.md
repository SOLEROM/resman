# Wiki Usage Guide

How to add data, capture ideas, and maintain the vault.

---

## 1. Ingest a URL (articles, reports, blog posts)

Use `injest.sh` from inside the vault directory:

```bash
# Basic — fetches the page, creates wiki pages, updates index + log
./injest.sh https://example.com/some-article

# With canvas update — also adds nodes to wiki/canvases/main.canvas
./injest.sh https://example.com/some-article --can
```

The script will:
1. Fetch the page content
2. Save the raw source to `.raw/articles/`
3. Create 2–4 wiki pages in the appropriate folder
4. Update `wiki/index.md`, `wiki/log.md`, and `wiki/hot.md`
5. Record the ingest in `.raw/.manifest.json` (prevents duplicate ingests)

**Translated sources** — if the page is not in English, the agent translates it automatically and notes the original language in the raw file.

---

## 2. Ingest a YouTube Talk

1. Copy the template:
   ```bash
   cp _templates/youtube-talk.md .raw/talk-speaker-topic.md
   ```

2. Open `.raw/talk-speaker-topic.md` and fill in the frontmatter (title, speaker, URL).

3. Paste the transcript:
   - On YouTube: click `…` under the video → **Open transcript**
   - Select all text, copy, paste between the `---` dividers in the file

4. Tell Claude Code:
   ```
   /wiki-ingest .raw/talk-speaker-topic.md
   ```
   Or to ingest everything pending at once:
   ```
   ingest all files in .raw/ with status: to-ingest
   ```

---

## 3. Drop a Raw File for Later Ingestion

For PDFs, copied text, or any source you want to process later:

- Drop the file into `.raw/`
- When ready: `ingest all files in .raw/ with status: to-ingest`

The ingest agent reads `status: to-ingest` frontmatter to find pending items and updates it to `ingested` when done.

---

## 4. Write an Idea Page

Copy the idea template and fill it in:

```bash
cp _templates/idea.md wiki/ideas/my-idea-name.md
```

Key fields to fill:
- `sector` — links the idea to one of the nine sector pages
- `stage` — `seed-idea` → `explored` → `validated` → `archived`
- `score` — 1–10 rough conviction score; drives dashboard sort order

Then add the idea to `wiki/ideas/_index.md` under the appropriate sector row.

---

## 5. Add a Research Note

```bash
cp _templates/research.md wiki/research/report-title.md
```

For short findings you don't need a full page for, just add a bullet to the relevant sector page under its **Key Problems** or **Market Landscape** section and link to the source URL inline.

---

## 6. Add a Player (Investor, Startup, Incumbent)

```bash
cp _templates/player.md wiki/players/company-name.md
```

Then add a row to the relevant table in `wiki/players/_index.md`.

---

## 7. Add a New Sector

```bash
cp _templates/sector.md wiki/sectors/new-sector.md
```

Then add it to `wiki/index.md` under **Sectors** and to `hot-cache.md`.

---

## 8. Add a Concept / Term

Open `wiki/concepts/_index.md` and add a bullet under the appropriate heading. No separate page needed unless the concept warrants deep coverage.

---

## Weekly Maintenance

Run these once a week to keep the vault healthy. Each takes 1–2 minutes.

### Lint the vault

```
/wiki-lint
```

Finds: orphan pages with no inbound links, dead wikilinks, frontmatter gaps, empty sections, stale `to-ingest` files. Reviews the output and fixes anything flagged.

### Review the ideas index

Open `wiki/ideas/_index.md`. For each idea:
- Has anything changed in the market since you last looked?
- Should the `stage` or `score` be updated?
- Are there open questions you can now answer?

### Check the ingest log

Open `wiki/log.md` and scan the last week's entries. Ask:
- Did any ingested source suggest a new idea worth capturing?
- Are there sector pages that should be updated with new data?

### Clear `.raw/`

Check for any files in `.raw/` still marked `status: to-ingest` — ingest or delete them. A clean `.raw/` folder means nothing is waiting.

### Update hot-cache

Open `hot-cache.md`. Remove links to pages you're no longer actively referencing. Add links to whatever you're actively working on this week. This file is loaded into Claude's context at the start of every session — keep it tight (under 20 links).

---

## Quick Reference

| Task | Command / Action |
|------|-----------------|
| Ingest a URL | `./injest.sh <url>` |
| Ingest URL + canvas | `./injest.sh <url> --can` |
| Ingest YouTube talk | Fill `_templates/youtube-talk.md` → drop in `.raw/` → `/wiki-ingest` |
| Ingest pending `.raw/` files | Tell Claude: `ingest all files in .raw/ with status: to-ingest` |
| New idea | `cp _templates/idea.md wiki/ideas/name.md` |
| New research note | `cp _templates/research.md wiki/research/name.md` |
| New player | `cp _templates/player.md wiki/players/name.md` |
| Health check | `/wiki-lint` |
| Save this conversation to wiki | `/claude-obsidian:save` |
