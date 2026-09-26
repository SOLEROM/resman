# The brief page — template and the six wiki modes

`wiki/meta/brief.md` is rewritten in full on every run in this shape. Every
section is present even when it is still open: then it carries the recommended
answer and the word *open*, so a later run sees what is undecided.

```markdown
---
type: meta
title: "Brief — <vault name>"
status: active
created: <keep from the first run; today on the first run>
updated: <today>
tags: [brief, meta]
interview: none | short | full
questions_asked: <N>
settings: "<the parameter tokens this run received>"
---

# Brief — <vault name>

## Purpose
<one sentence: the line the scaffold takes as its argument>

## Mode
<one of the six modes below, or a named combination, with one line of why>
Layout pin: `wiki/index.md`, `wiki/log.md` and `wiki/hot.md` stay at the wiki
root. resman and its skills read them there; the modes reference of the plugin
also shows a vault-root `_meta/` layout, which this vault does not use.

## Audience
<who reads the wiki: the operator, a team, Claude from other projects>

## Scope
### In
<what belongs here>
### Out
<what belongs in another vault; the URL classifier reads the hint's summary,
which comes from this section and Purpose>

## Seed domains
<the top-level topic areas; each becomes a domain page at scaffold time>

## Key questions
<what the wiki must be able to answer; deepList's first candidates>

## Sources
<expected material and where it arrives: URLs, feeds, folders, .raw/>

## Entities
<people, organisations, products, repositories to track from day one>

## Upkeep cadence
<lint, hot cache, deepList, ingest: how often. Recorded only; the operator
sets schedule.yaml>

## Related vaults
<names only, no cross-vault links>

## Seed (verbatim)
<the operator's text exactly as received, or "none">

## Interview
- **Q1** <question> — recommended: <answer> — taken: <the operator's answer, or "recommended">
- …
```

## The six modes of the claude-obsidian wiki skill

The plugin's `wiki` skill scaffolds from one of these (its `references/modes.md`
has the folder trees and frontmatter). Recommend the best fit; combining is
allowed when the folder names stay distinct.

| mode | use when | typical folders |
|---|---|---|
| Mode A: Website / Sitemap | a site's pages, structure, audits, keywords | `pages/`, `structure/`, `audits/`, `keywords/`, `entities/` |
| Mode B: GitHub / Repository | a codebase: modules, decisions, dependencies, flows | `modules/`, `components/`, `decisions/`, `dependencies/`, `flows/` |
| Mode C: Business / Project | a project or team: stakeholders, decisions, deliverables, intel | `stakeholders/`, `decisions/`, `deliverables/`, `intel/`, `comms/` |
| Mode D: Personal / Second Brain | goals, learning, people, life areas, resources | `goals/`, `learning/`, `people/`, `areas/`, `resources/` |
| Mode E: Research | papers, concepts, entities, an evolving thesis, gaps | `papers/`, `concepts/`, `entities/`, `thesis/`, `gaps/` |
| Mode F: Book / Course | a book or course: characters, themes, concepts, timeline, synthesis | `characters/`, `themes/`, `concepts/`, `timeline/`, `synthesis/` |

Whatever the mode, this vault keeps `index.md`, `log.md` and `hot.md` directly
under `wiki/`.
