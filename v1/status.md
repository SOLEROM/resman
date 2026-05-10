# resman — status snapshot

**Last updated:** 2026-05-10
**Branch:** main
**Tests:** 126 passing (`/tmp/resman-venv/bin/python -m pytest`)
**Server entry:** `./run.sh` (use `--vname /path/to/venv` to point at a non-default Python venv; pass `--public` to expose on LAN)

## Recent additions (post-Phase 6)

- **`--public` / `--host` flags** — expose Flask + ttyd on the LAN; CORS
  relaxed; iframe URLs derived from `window.location.hostname` so they work
  whether you load via 127.0.0.1, localhost, or LAN IP.
- **Wiki tab** (renamed from Docs) — defaults to `<vault>/wiki/overview.md`,
  with three canonical-page buttons in the toolbar (**Hot** / **Index** /
  **Overview**) plus reload. Endpoint: `GET /api/vaults/{name}/wiki`.
  Health check renamed: `wiki_home_exists` (`wiki/overview.md`) replaces the
  old `readme_exists` row. The per-vault `readme:` config field was removed.
- **Help tab** — file-tree of `<repo>/man/*.md` rendered with `marked.js`.
  Endpoints: `GET /api/help/tree` + `GET /api/help/page?file=…`. Pages live
  at `/mnt/resman/man/` and travel with the source.

---

## Where we are

The 4-phase plan from `design/00-README.md` is implemented end-to-end. Two
rounds of operator-feedback work and a Phase 5 polish pass are in. Phase 6
(verification) is half-done — the items that fit inside the test suite are
done; the items requiring a real browser or a different host machine are
not.

```
Phase 0  Pre-implementation validation         ✅
Phase 1  Obsidian Push + Core Shell            ✅
Phase 2  Terminal Sessions (ttyd)              ✅
Phase 3  Task Queue                            ✅
Phase 4  ALL-Vaults, Cron, Polish              ✅
Operator round 1 (folder picker, wizard,
  bootstrap-via-session, ttyd race, tab
  rename, theme, header tabs, --vname, …)     ✅
Phase 5  Finish placeholders                   ✅
Phase 6  Verify what's claimed                 🟡 in-repo done; live items pending
Phase 7  Production hygiene                    ⛔ not started
```

---

## What got built in Phase 5 (the most recent block)

| Feature | Files |
|---|---|
| Markdown-rendered Docs tab (marked.js from CDN) | `static/js/app.js` `loadDocs()`, `templates/index.html`, `static/css/style.css` `.docs-body` |
| `GET /api/vaults/{name}/readme` (path-traversal blocked via `Path.relative_to()`) | `modules/routes.py` |
| Open Obsidian button → `POST /api/vaults/{name}/open` (detached subprocess, vault path appended) | `modules/routes.py`, `static/js/app.js` `openVaultInObsidian()` |
| Vault health modal — clickable `⚠`/`?` warn icons | `static/js/app.js` `showVaultHealth()`, `style.css` `.health-table` |
| Compact-log button in Tasks toolbar | `static/js/app.js` `compactTasksLog()` |
| Cron-skip warning banner | `templates/index.html` `#cron-skip-banner`, `app.js` `showCronSkipBanner()` |
| Window-overrun "End window now" red button | `templates/index.html`, `app.js` `renderWindow()` |
| 9 new route tests (README + open-obsidian + health-extras) | `tests/test_routes.py` |

## What got built in Phase 6 (in-repo verification)

| Feature | Files |
|---|---|
| ALL-vault parent/child aggregation tests | `tests/test_task_manager.py` 3 new tests — fan-out, parent-fails-when-any-child-fails, dispatch_started ordering |
| JSONL crash-recovery tests | `tests/test_task_manager.py` 5 new tests — corrupt line, partial final line, running→interrupted, empty file, blank lines |

One real subtlety surfaced and is captured: `make_tm()` calls `replay()` on
the default log path during fixture setup, which would silently truncate any
hand-crafted partial line before the test's own replay() saw it. The new
helper `_write_corrupt_log()` writes to a sibling subdir to avoid the
collision.

---

## Where to pick up next time

### Phase 6 — physical verification still pending

These cannot be done from inside the test suite. They need a real browser
or a different host. Each one is independent of the others.

1. **Chrome iframe behavior** — only Firefox is verified. Open the app in
   Chrome, spawn a Claude session, watch for silent reconnect loops or 403s
   on the ttyd iframe. ttyd is launched with `--check-origin=false`; that
   should be enough, but Chrome's SameSite/SecFetch rules differ.
2. **Live ttyd race** — the `_wait_for_listen()` fix is unit-tested with a
   mock. To stress it, spawn 5+ sessions back-to-back on a slow VM and
   confirm none of the iframes show "connection refused".
3. **iCloud / Obsidian Sync vault** — the 60-second `_resman/status.md`
   write may cause sync churn or conflicts. Open question in
   `design/05-obsidian-push.md`. Needs an actual CloudKit-backed vault.
4. **Ubuntu 22 host run** — the snap → prebuilt-binary fallback chain in
   `deps.sh` and the broken-shebang detection in `run.sh` are written but
   only verified on Ubuntu 24. Run `./deps.sh --check && ./run.sh` on the
   Ubuntu 22 box.

### Phase 7 — production hygiene (only if you'll leave it running 24/7)

In rough priority order:

- `systemd --user` unit so `run.sh` survives reboot
- Server log file with rotation (currently goes to stdout; lost when the
  shell that started it closes)
- Automatic `system.yaml` backup before each save (atomic write protects
  against partial writes, not against bad edits)
- Surface task replay summary on the Health page UI (the data is in
  `/api/health` already; just not rendered)

### Phase 8 — open design questions still unresolved

- Blue dot (Obsidian-open per vault) — process inspection, platform-specific.
  Probably fine to drop the requirement entirely. Mark in
  `design/03-vault-registry.md` if so.
- `skip_count` reset semantics on successful cron fire — assumed yes in
  `design/08-scheduler.md` but not actually wired. Trivial to settle once
  decided.

---

## Things probably **not** worth building

- Auth — it's bound to `127.0.0.1`; the CSRF header is sufficient.
- Multi-host or clustering — explicitly out of scope.
- E2E browser test suite — useful but heavy. Manual smoke on Chrome covers
  the highest-value scenarios.

---

## Useful pointers

| Want to … | Look at |
|---|---|
| Understand the architecture top-down | `design/00-README.md` (index), `design/01-architecture.md` |
| Add a new endpoint | `control-plane/modules/routes.py` + a test in `tests/test_routes.py` |
| Change the SPA | `control-plane/static/js/app.js`, `templates/index.html`, `static/css/style.css` |
| Trace how a task event flows | `control-plane/modules/task_manager.py` `_apply()` (replay) and `_finalize()` (live) |
| Trace how a session is spawned | `control-plane/modules/session_manager.py` `spawn()` — note the `_wait_for_listen()` race fix and `initial_command` for the new-vault wizard |
| See what tmux options resman applies per session | `control-plane/modules/tmux_manager.py` `_apply_session_options()` |
| Run on a fresh host | `./deps.sh` then `./run.sh` (both accept `--vname /custom/venv`) |
| Run the test suite | `/tmp/resman-venv/bin/python -m pytest` |

---

## Recommended next single step

If picking this up cold, the highest-value 30-minute task is **Chrome
iframe verification** — it's the only blocker that could falsify the whole
ttyd-iframe approach. Open `http://127.0.0.1:5090` in Chrome with the
DevTools network panel open, spawn a Claude session, look for 4xx on the
ttyd port. If Chrome behaves, declare Phase 6 done and decide whether
Phase 7 is worth doing now or later.
