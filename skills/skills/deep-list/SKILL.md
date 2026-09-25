---
name: deep-list
description: >
  Rank the research values worth a dedicated deep-research run for this vault's
  objective and keep that list current in wiki/meta/deep-list.md: retire values
  the wiki has since filled, add new gaps, re-score what is left. Triggers on:
  deep list, research values, what should we research next, refresh the deep
  list, rank research topics, deepList.
---

# deep-list: the ranked research values of this vault

You are running inside an Obsidian wiki vault. The current working directory is
the vault root; the wiki lives in `./wiki/`. You write **one page**,
`wiki/meta/deep-list.md`, and touch nothing else except `wiki/log.md` and
`wiki/index.md`. Never modify `.raw/`, `.obsidian/`, `_resman/`, or any file
outside `wiki/`. Running this skill twice must not duplicate anything: the page
is rewritten in full every run, carrying its own history.

The page answers one question for the operator: *which topics, questions or
entities matter enough to this vault's objective that each deserves its own
deep-research run, in what order?* Each open value carries the exact
`/claude-obsidian:autoresearch …` line that would fill it. You do not trigger
that research; the operator does, from the Tasks view.

## Parameters

Parameters arrive as `key=value` tokens after the slash command (text values in
double quotes, lists as `"a|b|c"`). resman renders every one of them from the
operator's settings; if a token is missing, use the default.

| token | default | meaning |
|---|---|---|
| `list_size` | 15 | open values kept after ranking |
| `candidates_per_run` | 30 | new candidates considered per run before pruning |
| `min_score` | 40 | values scoring below this are dropped |
| `w_importance` | 40 | weight of "importance to the objective" |
| `w_gap` | 30 | weight of "how thin the coverage is" |
| `w_leverage` | 20 | weight of "how much it unblocks / how often it is mentioned" |
| `w_urgency` | 10 | weight of "time sensitivity" |
| `filled_min_pages` | 2 | pages needed before a value counts as filled |
| `filled_min_words` | 300 | body words a page needs to count toward "filled" |
| `focus` | "" | extra objective hint, appended to what the vault says about itself |
| `exclude` | "" | values never to propose (pipe-separated) |

Normalise the four weights so they sum to 1 before scoring.

## Procedure

Work through the steps in order. Read before you write.

### 0. Preconditions

- If `wiki/` does not exist: print `deep-list: no wiki/ in this vault — nothing
  written` and stop. Write nothing.
- Read, in this order and only as far as you need: `wiki/hot.md`,
  `wiki/index.md`, `wiki/overview.md`.

### 1. The objective

Derive what this vault is for from: the `Purpose:` and `Mode:` lines of the
vault's `CLAUDE.md` (the plugin's bootstrap writes them), `wiki/hint.json`
(`summary`, `tags`) when present, `wiki/overview.md`, and the `focus`
parameter. Write it as two or three sentences.

If the previous `wiki/meta/deep-list.md` has `objective_pinned: true` in its
frontmatter, keep its *Objective* section verbatim instead.

If none of the sources says anything and `focus` is empty: print
`deep-list: no objective found — set focus in Skills → resman skills →
deep-list, or write wiki/overview.md` and stop. Write nothing.

### 2. Coverage signals

Read the `_index.md` of every family folder under `wiki/` (`entities/`,
`concepts/`, `trends/`, `signals/`, `domains/`, `sources/`, `questions/`,
whatever exists), the newest `wiki/meta/lint-report-*.md` if any (its *missing
pages* and *orphans* lists), and the top 30 entries of `wiki/log.md` (what
changed since the last run). Note page sizes: a page under `filled_min_words`
body words is *thin*.

### 3. The previous list

If `wiki/meta/deep-list.md` exists, parse it:

- the *Open values* table: value, score (with its trend mark), `since`
  (first-seen date and the `×N` runs-seen count), the `done` column;
- the *Filled* and *Dismissed* sections;
- the *Run log*.

Honour the operator's marks in the *Open values* table: a row whose checkbox is
ticked (`[x]`) or whose status column says `done` counts as **filled**; a row
whose status says `dismissed` goes to *Dismissed* and is never proposed again.
Anything in the `exclude` parameter is dismissed too.

### 4. Retire what is filled

For every open value, search `wiki/**/*.md` for pages about it: title,
aliases, tags, headings, wikilinks. A value is **filled** when at least
`filled_min_pages` pages of at least `filled_min_words` body words each exist
that were created or updated after the value's `first_seen`. Move filled values
to *Filled* with today's date and `[[wikilinks]]` to the pages that filled them.

### 5. Candidates

Collect at most `candidates_per_run` new candidates from: concepts or entities
mentioned in two or more pages that have no page of their own; thin pages;
open questions in `overview.md`, `questions/` or `question`-type pages; trends
and signals with no concept coverage; lint's missing pages; and a decomposition
of the objective into what a domain expert would need this vault to know.
Skip anything dismissed or excluded.

### 6. Score and rank

Score every open value and candidate 0–100 on four axes, then combine with the
normalised weights:

- **importance**: how central it is to the objective;
- **gap**: how thin the current coverage is (no page = 100, a rich cluster = 0);
- **leverage**: how many pages mention it or would link to it; what it unblocks;
- **urgency**: recency of signals, time sensitivity.

The four axis scores are working values: use them to rank, do **not** write
them into the page — only the combined score goes in the table, with a trend
mark against the value's previous score: `↑` (+5 or more), `↓` (−5 or less),
`→` otherwise, `new` for a value seen for the first time. Drop values below
`min_score`. Keep the top `list_size`. An existing value keeps its first-seen
date and gets its runs-seen count incremented; a new value gets today's date
and a count of 1.

### 7. Write the page

Rewrite `wiki/meta/deep-list.md` in full with this shape:

```markdown
---
type: meta
title: "Deep list — research values"
created: <keep from the first run; today on the first run>
updated: <today>
tags: [deep-list, meta]
run_count: <previous + 1>
objective_pinned: <keep the previous value; false on the first run>
settings: "<the parameter tokens this run received>"
---

# Deep list — research values

## Objective

<two or three sentences>

## Open values

| # | done | value | score | since | why | related | research with |
|---|---|---|---|---|---|---|---|
| 1 | [ ] | [[Page]] or *page name it would get* | 87 ↑ | 2026-09-24 ×3 | one line | [[a]], [[b]] | `/claude-obsidian:autoresearch <topic>` |

*score*: combined 0–100 and the trend since the previous run (↑ ↓ → or new). *since*: first seen ×runs seen.

Keep the table this narrow: eight columns, one line per value, no per-axis
scores. The `done` column is the operator's: leave the checkbox unticked;
they tick it or write `done` / `dismissed` there to steer the next run.

## Filled

- <date> — **<value>** → [[page]], [[page]]  (newest first; keep the last 50)

## Dismissed

- **<value>** — <why, or "by the operator">

## Run log

- <date>: <open> open, <filled> filled, <new> new  (newest first; keep the last 10)
```

Every row's *research with* cell is a complete, copy-ready
`/claude-obsidian:autoresearch <topic>` line whose topic is specific enough to
run as-is. Use Obsidian-flavored markdown; link with `[[Note Name]]`.

### 8. Bookkeeping

- Prepend one line at the **top** of `wiki/log.md`:
  `- <date> deep-list: N open, M filled, K new`.
- On the first run add the page to `wiki/index.md` under the meta section.
- Print exactly one line to finish:
  `deep-list: N open, M filled, K new → wiki/meta/deep-list.md`.

## Rules

- One page, rewritten in full, with its own history; no dated copies, no
  sidecar files, no JSON.
- Only `wiki/meta/deep-list.md`, `wiki/log.md` (top-append) and
  `wiki/index.md` (first run) change. Nothing outside `wiki/`.
- Do not start the research yourself. Do not fetch the web. Do not ask
  questions: this runs non-interactively.
- Frontmatter on the page; Obsidian markdown; `[[wikilinks]]`.
