# Reimplementation Plan — porting garage features into resman

> **STATUS: all tasks implemented & tested (282 pytest passing, +31 from baseline).**
> Live-verified in a browser: 3-way theme switch, footer schedule + weekly bar,
> ⊞ Windows modal, wiki tree unread dots + selection box, search, random, read
> toggle. See "Outcome" at the bottom.

**Reference app:** `/data/agents/garage` (Next.js/React + TS monorepo).
**Target app:** this repo — Python control-plane (`control-plane/`) + vanilla-JS SPA
(`static/js/app.js`, `static/css/style.css`, `templates/index.html`).

The reference is **authoritative for behaviour and look-and-feel**; we port the
*concepts and palettes*, adapted to our Python + vanilla-JS architecture (we do
**not** port React components, Next.js API routes, or garage-specific install
infra like sudo wrappers / bun / `usage.sh` cron sampling).

The current app already ships *partial* versions of all three features, so most
of this is closing gaps rather than greenfield work.

## Execution order (re-ordered for efficiency / risk)

Lowest-risk, highest-visible-value first; the big architectural change last.

1. **Task 1 — Themes** (self-contained, additive). ✅ done first.
2. **Look & feel — wiki tree jump + selection highlight** (small; shares surface with Task 3).
3. **Task 3 — Wiki read/unread + search + random read** (additive backend + UI).
4. **Task 2 — Window management (cld20 model)** (largest; touches `window_state`, tasks, footer, top bar).

Each task ends with: run the test suite, no regressions, update docs.

---

## Task 1 — Green / Black / White themes

**Garage:** three themes via `data-theme` on `<html>`: `green` (default phosphor),
`dark` (white-on-black), `light` (black-on-white); 3-way segmented control;
persisted to `localStorage`. We already have `dark`/`light` under `resman-theme`.

**Plan:**
- Add `:root[data-theme="green"]` block to `style.css` using garage's **exact green
  palette** mapped onto our existing variable names (`--bg-base`, `--text-primary`,
  `--accent`, status colours…).
- Keep existing `dark` = "black" and `light` = "white" options (already polished).
- Replace the single moon/sun cycle button with a **3-way segmented control**
  (green ● / dark ◐ / light ○) matching the ref, in the header status area.
- JS: `setTheme(name)` + active-state rendering; update FOUC inline script to
  accept `green`. Keep `resman-theme` localStorage key.

**Done when:** all three themes switch cleanly, survive reload, no FOUC.

---

## Look & feel — wiki tree

- **Jump to reading page:** clicking a tree node already calls `loadWiki(path)`.
  Verify it works and that the selected page scrolls into view.
- **Selection color box:** ensure the active tree node has a clear highlighted
  box (`.wiki-file.active`), matching garage's `.active` treatment.

---

## Task 3 — Wiki read/unread

**Garage model:** existence of a sidecar marker `.{stem}.unrd` next to each
`page.md` = *unread*; baseline `.unrd-scan` mtime = last reconcile; opening does
**not** auto-mark; explicit toggle button; `random` picks a random unread page;
search weights titles 5× over body. Marker files survive rsync (we share the same
wiki pages via rsync between garage-resman and this project).

**Plan (adapted to Python + vanilla JS):**
- Backend module `wiki_unread.py`: `reconcile(wiki_dir)`, `list_unread`,
  `mark_read`, `mark_unread`, `is_unread`, `pick_random_unread`. Markers live as
  `.{stem}.unrd` colocated with pages; baseline `.unrd-scan`. Path-traversal safe.
- Backend module / route additions: search endpoint (titles 5× + body, AND tokens,
  capped results, snippet with `<mark>`).
- API routes:
  - `GET  /api/vaults/<name>/wiki/tree` → include `unread: bool` per file (reconcile first).
  - `POST /api/vaults/<name>/wiki/read` `{file, read}` → toggle marker, return state.
  - `GET  /api/vaults/<name>/wiki/random` → random unread relpath.
  - `GET  /api/vaults/<name>/wiki/search?q=` → ranked hits.
- Frontend: tree shows unread indicator (dot/asterisk) cascading to folders; a
  read/unread toggle on the open page; **Search** box + **Random** button in the
  wiki toolbar; mark-read action refreshes tree stars.

**Done when:** unread markers reconcile on tree load, toggle persists, search +
random work, indicators render, rsync-added pages show as unread.

---

## Task 2 — Window management (cld20 model)

**Garage cld20:** 5 daily 5-hour windows aligned to Claude session windows
(`server_start` hour + `night_window` flag each), a weekly anchor (weekday+hour),
usage sampled 5×/day via cron→`usage.sh` into JSONL, charts, settings cards, and a
public API so the dispatcher can schedule `night_window` tasks.

**Scope decision:** port the **window model + management + task wiring + config UI**;
do **not** port the garage-infra-specific cron usage-sampling/charts (sudo, bun,
`usage.sh`, install paths) — that doesn't fit our Python control-plane and would be
fragile. We keep our manual active/between/ended gate but enrich it with the cld20
*concepts*.

**Plan:**
- Extend window config (new `window_config` in `resman.yaml` or a JSON sidecar):
  - `windows: [{ server_start: 0..23, night_window: bool }, …]` (default `[0,5,10,15,20]`).
  - `weekly_anchor: { weekday, hour }`.
  - derive **current** + **next** window from the clock.
- `window_state.py` (or new `window_manager.py`): compute current/next window,
  keep manual active/between override, expose checks/log (a rolling history of
  window open/close + task-gating events).
- **Refactor footer:** show current window (index, range), next window countdown,
  weekly-cycle progress — replacing the single "Window: ACTIVE" line.
- **Top-bar config:** a "Windows" config control (modal or Config sub-tab) to edit
  all window params (the 5 starts, night flags, weekly anchor).
- **Connect to tasks:** keep deferred-promotion on activation; add `night_window`
  scheduling (a task can target the next night window).
- **Checks/logs display:** a panel listing recent window events / gating decisions.

**Done when:** windows are configurable from the top bar, footer reflects the
cld20-style current/next/weekly view, task gating still works, logs/checks visible,
tests green.

---

## Cross-cutting constraints

- Don't break existing features; run `pytest` after each task.
- Keep files focused (<800 lines); add tests for new backend modules.
- Update `docs/design/*` and `man/*` as features land.

---

## Outcome (implemented)

| Task | What shipped | Tests |
|------|--------------|-------|
| **1 — Themes** | `green` palette (garage values) + 3-way segmented switch (green/dark/light); FOUC script + `setTheme()`; persisted under `resman-theme`. | n/a (frontend) |
| **Look & feel** | Tree click jumps + scrolls into view; `.wiki-file.active` selection box with accent rail. | covered via tree tests |
| **3 — Read/unread** | `modules/wiki_unread.py` (markers + reconcile + search + random); routes `…/wiki/read`, `/random`, `/search`, `unread` flag on tree; tree dots + folder cascade + toolbar search/random/read-toggle. | `test_wiki_unread.py` (11) + 6 route tests |
| **2 — Windows** | `modules/window_schedule.py` (configurable daily windows + weekly anchor + offset + length); routes `GET/PUT /api/window/schedule`, `/next-night`; footer current/next/weekly bar; top-bar **⊞ Windows** modal (edit + checks + log); **🌙 Night window** task scheduling. | `test_window_schedule.py` (9) + 5 route tests |

**Files added:** `control-plane/modules/wiki_unread.py`,
`control-plane/modules/window_schedule.py`, `docs/design/13-window-schedule.md`,
`docs/design/14-wiki-read-unread.md`, `tests/test_wiki_unread.py`,
`tests/test_window_schedule.py`.
**Files changed:** `templates/index.html`, `static/js/app.js`,
`static/css/style.css`, `modules/routes.py`, `server.py`, `tests/test_routes.py`,
`man/wiki.md`, `man/window-state.md`, `man/index.md`, `docs/design/00-README.md`.

**Deliberately not ported:** cld20's usage-sampling pipeline (cron → `usage.sh`
→ JSONL → charts) — tied to garage's install infra (sudo, bun, fixed paths);
doesn't fit this Python control plane. We ported the window *model* + management.

**New persistence:** `config/window_schedule.json`; per-page `.<name>.unrd`
markers + `.unrd-scan` baseline inside each vault's `wiki/`.
