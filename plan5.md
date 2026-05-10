# resman — Tasks UX Redesign Plan (v5)

**Date:** 2026-05-10
**Scope:** Rethink the Tasks tab. Make it easy to trigger an operation against
one vault or all vaults, watch the resulting Claude run live, and either run
on-demand or schedule for later.
**Effort:** /effort high — full-rewrite of the Tasks UI; surgical backend
additions for streaming + cancel + scheduled-state.

---

## 0. TL;DR

The current Tasks tab is a flat table with a single "+ New Task" modal that
asks the user for raw JSON params. Most resman tasks are `claude -p '<plugin
command>'` runs against a vault, which means three things matter most:

1. **Pick a vault (or ALL) and pick an operation in two clicks.**
2. **Watch the log live** while it runs — and be able to **kill it** if it
   misbehaves.
3. **Defer to a specific clock time** when needed (e.g., "run autoresearch
   tonight at 23:00") — without dropping into `schedule.yaml`.

This plan delivers those in two phases:

- **Phase A (v1, ~2 days)**: per-operation form, vault/ALL selector, single
  `scheduled_for` datetime input, live log streaming over Socket.IO, cancel
  running tasks, log size cap. Existing flat-table UI gets replaced inline;
  no split-pane yet.
- **Phase B (v2, ~3 days)**: split-pane operations-first layout, sidebar
  per-vault `▶` menu enriched with operations, recurring-task UI on top of
  `schedule.yaml`, "missed schedule" recovery.

Phase B is optional polish; Phase A on its own is shippable and answers the
user's three needs.

---

## 1. Why the current UI fails

Read `v1/control-plane/templates/index.html:110-137` and
`v1/control-plane/static/js/app.js:396-506`.

| Friction | Today | Why it hurts |
|---|---|---|
| Trigger a wiki-lint on `vla6` | Click Tasks → `+ New Task` → set name → pick vault from dropdown → pick operation → write empty `{}` JSON → submit | 6 clicks + free-text JSON |
| Trigger same on ALL | Same modal, change vault to `ALL` | Easy to forget; modal is identical to single-vault path |
| Watch the log | Click `log` action → modal opens with a static `<pre>` snapshot | No live tail; close modal and re-open to refresh |
| Kill a runaway task | No way. `cancel()` rejects `running` state in `task_manager.py:447-457` | Forces the user to `kill -9` from a shell |
| Schedule for 23:00 | Edit `schedule.yaml` with a cron expression | Power-user path only |
| Per-operation params | Free-text JSON textarea for everything | One typo = task fails on bad params |

The redesign attacks each row directly.

---

## 2. Mental model: operations are verbs, tasks are instances

The user thinks in **operations** ("lint the wiki", "ingest this URL",
"autoresearch X") not in tasks. The new UI puts operation choice up front and
treats the queue as the audit trail of what those operations produced.

Operation set is fixed in `v1/control-plane/modules/task_manager.py:46-54`
and `v1/control-plane/modules/plugin_commands.py`. We expose it as a static
JS constant on the frontend — **no `/api/operations` endpoint**; it doesn't
change at runtime, and adding an HTTP round-trip just to learn what's
hard-coded next door is YAGNI.

```js
// app.js (v5)
const OPERATIONS = {
  "wiki-lint":             { label: "Lint wiki",          group: "Wiki",     params: [] },
  "wiki-update-hot-cache": { label: "Update hot cache",   group: "Wiki",     params: [] },
  "wiki-bootstrap":        { label: "Re-run bootstrap",   group: "Wiki",     params: [], note: "non-interactive re-run only" },
  "wiki-ingest":           { label: "Ingest URL",         group: "Research", params: [{ key: "url",    type: "url",     required: true,  label: "URL" }] },
  "wiki-autoresearch":     { label: "Autoresearch topic", group: "Research", params: [{ key: "topic",  type: "text",    required: true,  label: "Topic", maxLength: 200 }] },
  "run-prompt":            { label: "Run a Claude prompt",group: "Custom",   params: [{ key: "prompt", type: "text",    required: true,  label: "Prompt", maxLength: 200 }] },
  "run-shell":             { label: "Run shell command",  group: "Custom",   params: [{ key: "cmd_parts", type: "argv", required: true, label: "Argument list" }], confirm: "run-shell executes an arbitrary command in the vault directory. Proceed?" },
};
```

The form is rendered from `params[]` for the chosen operation. No more
free-text JSON.

---

## 3. UX (Phase A — what ships first)

The existing `<section id="tab-tasks">` becomes:

```
┌─────────────────────────────────────────────────────────────────────┐
│  Tasks                                                              │
├─────────────────────────────────────────────────────────────────────┤
│  ┌── trigger ─────────────────────────────────────────────────┐    │
│  │ Vault: [vla6 ▾]   ☐ Run on all vaults                      │    │
│  │ Operation: [Lint wiki ▾]                                   │    │
│  │ ┌── params ────────────────────────────────────────────┐   │    │
│  │ │ (URL field appears when operation = wiki-ingest)     │   │    │
│  │ │ (topic field when wiki-autoresearch, etc.)           │   │    │
│  │ └──────────────────────────────────────────────────────┘   │    │
│  │ Priority: ◉ medium ○ high ○ low                            │    │
│  │ When:     [datetime input — empty = run now]               │    │
│  │                                              [ Run task ]  │    │
│  └────────────────────────────────────────────────────────────┘    │
│                                                                     │
│  Queue (filter: ◉ active ○ all ○ recent)                            │
│  ▶ vla6  · wiki-lint        · 2m 14s   ●        [log ▾] [cancel]    │
│  ⏰ ALL  · wiki-update…     · scheduled 23:00   [log ▾] [cancel]    │
│  ⌛ vla6 · wiki-autoresearch · pending          [log ▾] [cancel]    │
│  ✓ vla6 · wiki-ingest       · 2h ago            [log ▾] [re-run]    │
│  ✗ rsm6 · wiki-lint         · 3h ago            [log ▾] [re-run]    │
└─────────────────────────────────────────────────────────────────────┘
```

Key UX points:

- **The vault is selected at the top**, defaulting to `state.selectedVault`.
  Toggling "Run on all vaults" disables the vault dropdown and submits with
  `vault: "ALL"`.
- **One scheduling control** — a single `<input type="datetime-local">`. Empty
  → run now. Non-empty in the future → `scheduled_for`. Non-empty in the past
  → reject in the form. (We do **not** add radios for "Defer to next window"
  — that behavior is already implicit in the priority/window-gating logic at
  `task_manager.py:369-374` and `_on_window_activated` at line 418, and
  surfacing it as a separate UX control duplicates what `priority` already
  means.)
- **Inline live log**: clicking `log ▾` on a row expands it into an inline
  pane that streams `task_log_appended` chunks for that `task_id`. Auto-tail.
  Re-clicking collapses. No modal, no split-pane (defer that to Phase B).
- **Cancel works for running** tasks too — sends `terminate()` then `kill()`
  after a 5 s grace. Existing pending/deferred cancellation still works.
- **Re-run**: same form, prefilled with the original task's params. Same as
  today, but the form is the new per-op form.

---

## 4. Backend changes (Phase A)

### 4.1 Live log streaming

`v1/control-plane/modules/task_manager.py:702-716` — replace the blocking
`proc.wait(timeout=3600)` with a line-chunk reader that emits to the bus.

```python
def _default_runner(cmd, cwd, log_file, bus, task_id, max_bytes=5*1024*1024):
    proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, bufsize=1, text=True)
    written = 0
    truncated = False
    with log_file.open("w", encoding="utf-8") as logf:
        logf.write(f"$ {' '.join(shlex.quote(x) for x in cmd)}\n")
        logf.write(f"cwd: {cwd}\n\n")
        for line in iter(proc.stdout.readline, ""):
            if written < max_bytes:
                logf.write(line)
                logf.flush()
                written += len(line.encode("utf-8"))
                bus.emit("task_log_appended", {"task_id": task_id, "chunk": line})
            elif not truncated:
                marker = f"\n... [output capped at {max_bytes} bytes; tail discarded]\n"
                logf.write(marker)
                bus.emit("task_log_appended", {"task_id": task_id, "chunk": marker})
                truncated = True
    return proc.wait()
```

The runner gets two new params (`bus`, `task_id`); injectable runners stay
testable. The 5 MB cap protects the browser from a runaway claude session
emitting GB of output (a real failure mode — anyone who's seen the wiki
plugin loop on a large vault knows this).

`websocket_handlers.py` already broadcasts `task_updated` from the bus — add
`task_log_appended` to the same passthrough.

### 4.2 Cancel running tasks

The runner doesn't currently expose the `Popen` handle. Two changes:

1. `TaskManager` gains `self._procs: Dict[str, subprocess.Popen]` populated
   at start of `_execute()` and cleared in `_finalize()`.
2. `cancel()` at `task_manager.py:447` learns to handle `running`:

```python
def cancel(self, task_id):
    task = self._tasks.get(task_id)
    if not task: return False
    if task.state == "running":
        proc = self._procs.get(task_id)
        if proc:
            proc.terminate()
            try: proc.wait(timeout=5)
            except subprocess.TimeoutExpired: proc.kill()
        # _finalize will write the failed/cancelled event
        return True
    if task.state in ("pending", "deferred", "scheduled"):
        # existing path
        ...
```

Decision: cancellation of `running` writes a `cancelled` event (not
`failed`); audit trail says "user killed it". Add `scheduled` to the list.

### 4.3 New state: `scheduled`

Adding `scheduled_for` as a *field* on `pending` tasks would create a hole —
`POST /api/tasks/{id}/promote` (`task_manager.py:435`) would let a user
fire-early past the schedule. Add it as a discrete state instead.

Changes:

- `STATES` (line 36) gains `"scheduled"`.
- New event in the JSONL log: `scheduled` (alongside `created`), payload
  `{ scheduled_for: ISO8601 }`.
- `create_task()` accepts optional `scheduled_for: str`. When set:
  - Reject if `vault == "ALL"` for v1 (parent/child + scheduling combinatorics
    not worth it). Phase B can add it.
  - Reject if the time is in the past (validate at API boundary too).
  - Skip the window-active dispatch path; write the `created` event then
    `scheduled` event; transition straight to `state="scheduled"`.
- APScheduler gets a one-shot `DateTrigger` that fires `promote(task_id)`
  when the moment arrives. `promote()` (line 435) accepts `scheduled` in
  addition to `deferred`.
- `_on_window_activated` (line 418) explicitly skips `scheduled` (it
  currently checks `!= "deferred"`, so it's safe by accident — write the
  test).
- `cancel()` accepts `scheduled` (above).
- **Replay path** (line 173): a `scheduled` task whose `scheduled_for` is in
  the past at startup → emit a "missed schedule" warning into
  `_integrity_warnings` and either auto-promote or leave for the user. v1
  choice: **auto-promote**, log the warning. Symmetric with how
  `interrupted` surfaces but doesn't block.

### 4.4 Replay change: detect `interrupted` more precisely

Today, line 204-209 marks every `running` task as `interrupted` on replay.
That's correct after a control-plane crash, but if we ever survive a config
reload the heuristic mislabels live processes. The runner now records the
PID into the `started` event payload; replay does `os.kill(pid, 0)` to
detect liveness:

```python
# inside replay, after applying all events:
for tid, task in self._tasks.items():
    if task.state == "running":
        pid = task.pid  # populated from started event
        if pid and _pid_alive(pid):
            continue  # process survived, leave it as-is
        task.state = "interrupted"
        ...
```

Tiny change, but it makes the live-log feature trustworthy across reloads.

### 4.5 API surface delta

Update `v1/control-plane/modules/routes.py` and `design/09-api.md`:

| Route | Change |
|---|---|
| `POST /api/tasks` | Accepts optional `scheduled_for: ISO8601`; validates not-past + not `ALL`. |
| `DELETE /api/tasks/{id}` | Now also kills running tasks (terminate + 5 s grace + kill). Writes `cancelled` event. |
| (Socket.IO) `task_log_appended` | New event. Payload `{ task_id, chunk }`. Browser appends to inline `<pre>` for the matching task row. |

No new endpoints. No SSE. The Socket.IO transport is already in the page
load; piggyback on it.

---

## 5. Frontend changes (Phase A)

All in `v1/control-plane/templates/index.html` (lines 110-137 replaced) and
`v1/control-plane/static/js/app.js` (`showNewTaskModal` at line 461 deleted).

### 5.1 Replace the trigger modal with an inline panel

The current modal is overhead for a two-click action. The new trigger panel
lives at the top of the Tasks tab, **always visible**.

Markup (replaces lines 110-137):

```html
<section id="tab-tasks" class="tab-panel">
  <div id="cron-skip-banner" class="cron-skip-banner" hidden>…</div>

  <div class="task-trigger">
    <div class="trigger-row">
      <label>Vault</label>
      <select id="t-vault"></select>
      <label class="all-toggle">
        <input type="checkbox" id="t-all"> all vaults
      </label>
    </div>
    <div class="trigger-row">
      <label>Operation</label>
      <select id="t-op"></select>
    </div>
    <div class="trigger-row" id="t-params-row">
      <!-- per-operation form fields injected here -->
    </div>
    <div class="trigger-row">
      <label>Priority</label>
      <select id="t-pri">
        <option value="medium" selected>medium</option>
        <option value="high">high</option>
        <option value="low">low</option>
      </select>
      <label>When</label>
      <input type="datetime-local" id="t-when" placeholder="empty = run now">
    </div>
    <div class="trigger-row trigger-actions">
      <span id="t-error" class="muted"></span>
      <div class="spacer"></div>
      <button id="btn-task-run" class="btn btn-accent">Run task</button>
    </div>
  </div>

  <div class="task-queue">
    <div class="toolbar">
      <select id="task-priority-filter">…</select>
      <select id="task-state-filter">
        <option value="active">active (default)</option>
        <option value="all">all</option>
        <option value="recent">recent (24h)</option>
      </select>
      <div class="spacer"></div>
      <button id="btn-task-compact" class="btn btn-sm">Compact log</button>
    </div>
    <div id="task-list"></div>
  </div>
</section>
```

The trigger panel is a real form; the queue is a card list (not a table —
table is too dense for inline log expansion). Each card:

```
  ▶ vla6 · wiki-lint · 2m 14s ●               [log ▾] [cancel]
  ╰─ when expanded:
     ┌── params ───────────────────────────┐
     │ {}                                  │
     └─────────────────────────────────────┘
     ┌── log (live) ───────────────────────┐
     │ $ claude -p /claude-obsidian:wiki-… │
     │ …                                   │
     │ …                                   │
     └─────────────────────────────────────┘
```

### 5.2 Per-operation form rendering

```js
function renderOpForm() {
  const op = OPERATIONS[$("#t-op").value];
  const row = $("#t-params-row");
  row.innerHTML = (op.params || []).map(p => {
    if (p.type === "url" || p.type === "text")
      return `<label>${esc(p.label)}</label>
              <input id="t-p-${esc(p.key)}" type="${p.type === "url" ? "url" : "text"}"
                     ${p.required ? "required" : ""} ${p.maxLength ? `maxlength=${p.maxLength}` : ""}>`;
    if (p.type === "argv")
      return `<label>${esc(p.label)} (one per line)</label>
              <textarea id="t-p-${esc(p.key)}" rows="3"></textarea>`;
  }).join("");
}
```

Submit handler builds `params` from the rendered fields by `key`. No JSON.

### 5.3 Live log subscription

```js
sock.on("task_log_appended", (msg) => {
  const pre = $(`#log-${msg.task_id}`);
  if (!pre) return;
  pre.textContent += msg.chunk;
  if (pre.dataset.autoscroll !== "off")
    pre.scrollTop = pre.scrollHeight;
});
```

Expanding `[log ▾]` on a row creates the `<pre id="log-${tid}">` and seeds
it from `GET /api/tasks/{id}/log` (full backlog), then leaves it open for
incremental chunks.

### 5.4 Vault dot — running tasks already light yellow

No change needed; current `vaultColor()` (line 72-79) already treats any
`running` task on a vault as yellow. Cancel transitioning to `cancelled`
will drop the dot back to gray automatically once the queue refreshes.

---

## 6. Phase B (defer — sketch only)

Once Phase A ships and we've used it for a week, consider:

- **Sidebar `▶` menu enrichment** — replace the prompt() with a popover
  listing the same operations, prefilled to that vault.
- **Split-pane Tasks layout** — left = queue list with selection, right =
  pinned detail with persistent live log. Useful when a task takes >10 min
  and you switch to other tabs.
- **Recurring-task UI** — a small "Schedules" sub-panel that lists
  `schedule.yaml` cron entries with enable/disable, next-fire time, and
  add/edit. Backed by `GET/POST/DELETE /api/cron/{id}`. Today users edit
  the YAML directly — fine if rare.
- **ALL + scheduled_for** — currently rejected in v1. v2: schedule the
  parent; child creation happens at fire time inside the dispatch lock.
- **"Missed schedule" UI** — instead of auto-promoting, surface in a
  banner with `[run now]` / `[reschedule]` actions.

---

## 7. Test plan

`tests/test_task_manager.py`:

- New runner streams `task_log_appended` chunks: spy on `bus.emit`, run a
  fake `echo a; echo b`, assert two chunks emitted.
- Log cap: runner with `max_bytes=64` against a long-output process truncates
  with marker, emits final marker chunk, no further chunks.
- Cancel running: start a `sleep 30` task, call `cancel(tid)`, assert
  `cancelled` event written within 6 s and `state == "cancelled"`.
- `scheduled` state: create with `scheduled_for=now+5s`, assert
  `state == "scheduled"`; advance APScheduler clock; assert promotion to
  `pending` then dispatch.
- Reject `scheduled_for + vault=ALL`: `create_task(..., vault="ALL",
  scheduled_for=...)` raises `ValueError`.
- Reject past `scheduled_for`: same.
- Replay missed schedule: write a `created` + `scheduled` event with a
  past time, replay, assert task auto-promoted (or warning emitted, depending
  on the v1 choice).
- Replay PID liveness: `started` event with a live PID → still `running`
  after replay; dead PID → `interrupted`.

`tests/test_routes.py`:

- `POST /api/tasks` accepts `scheduled_for`; rejects past/ALL combinations.
- `DELETE /api/tasks/{id}` on a running task triggers cancel path (mock the
  TM).
- Existing `task_updated` / new `task_log_appended` events surface in the
  Socket.IO test harness if there is one.

`tests/test_session_manager.py`: untouched.

Manual smoke (Phase A):

1. Pick `vla6`, `wiki-lint`, leave `When` empty, click Run task. Card
   appears with `running`. `[log ▾]` shows `$ claude -p …` then live
   output.
2. Toggle "all vaults", `wiki-update-hot-cache`, click Run. Parent + N
   children appear; each child's log opens independently.
3. Click `cancel` on a running child. Within 6 s state flips to
   `cancelled`. Parent re-aggregates to `failed`.
4. Set `When` to 30 s in the future. State = `scheduled` with timestamp.
   Wait. Auto-promotes; dispatches.
5. Set `When` to 30 s in the past. Form refuses (red error string).
6. Toggle "all vaults" + set `When`. Form refuses.
7. Trigger 10× `wiki-lint` then refresh the page. Active row reconnects to
   live log stream after reload.

---

## 8. Files touched

Backend:

- `v1/control-plane/modules/task_manager.py` — runner streaming, log cap,
  cancel running, `scheduled` state, PID-aware replay.
- `v1/control-plane/modules/routes.py` — accept `scheduled_for`, validate.
- `v1/control-plane/modules/scheduler.py` — register one-shot DateTrigger
  on task creation; cancel it on cancel.
- `v1/control-plane/modules/websocket_handlers.py` — passthrough
  `task_log_appended`.
- `v1/control-plane/server.py` — pass bus into the runner factory.

Frontend:

- `v1/control-plane/templates/index.html` — replace `<section id="tab-tasks">`.
- `v1/control-plane/static/js/app.js` — delete `showNewTaskModal`, add
  `OPERATIONS` constant, `renderOpForm`, inline log expand/subscribe.
- `v1/control-plane/static/css/style.css` — `.task-trigger`, `.task-queue`,
  `.task-card`, `.task-card.expanded`, `.task-log-pane`, `.all-toggle`.

Docs:

- `design/06-task-management.md` — `scheduled` state, log streaming,
  cancel-running semantics, log size cap.
- `design/09-api.md` — `scheduled_for` on `POST /api/tasks`,
  `task_log_appended` Socket.IO event, `DELETE /api/tasks/{id}` cancels
  running.
- `design/10-frontend.md` — Tasks tab redesign (replace the existing
  Tasks-tab paragraphs).
- `man/tasks.md` — rewrite to match the new flow.
- `status.md` — add a "Phase 8: Tasks UX redesign" line.

---

## 9. Decisions captured

| Question | Decision | Rationale |
|---|---|---|
| Operation registry: API or static JS constant? | **Static JS constant** | Operation set is hard-coded in `plugin_commands.py`; no runtime change. |
| Operation grouping: cards or flat select? | **Flat `<select>` grouped by `<optgroup>`** | 7 ops; cards are decoration. |
| Scheduling: one datetime input or radios? | **Single datetime input** | Empty = now; non-empty = scheduled. Window-defer is implicit via priority. |
| `scheduled_for` as field or state? | **State** | Field would let `promote()` fire early, bypassing schedule. |
| Run claude tasks in tmux+ttyd? | **No, keep Popen** | `claude -p` is non-interactive; tmux duplicates state machine. |
| Log transport? | **Socket.IO `task_log_appended`** | Already wired; SSE would be a third transport. |
| Log size cap? | **5 MB, marker line, drop tail** | Runaway plugin output → browser OOM otherwise. |
| ALL + scheduled_for in v1? | **Rejected** | Parent/child + schedule combinatorics not worth it for v1. |
| Missed schedule on replay? | **Auto-promote + warn** | Symmetric with `interrupted`; doesn't block startup. |
| Cancel a running task? | **terminate → 5 s → kill, write `cancelled` event** | The single biggest UX gap today. |
| Recurring-task UI? | **Phase B** | Today users edit `schedule.yaml` directly via the Config tab — fine for now. |
| Sidebar `▶` per-vault op menu? | **Phase B** | Form's vault dropdown covers it for v1. |

---

## 10. Open questions

- **Promote-now button on a `scheduled` task?** Symmetric with deferred —
  probably yes, behind the existing `[promote]` action. Cheap to add.
- **Re-run preserves `scheduled_for`?** No; re-run defaults to "run now"
  unless the user re-enters a time. Mirrors the priority/params prefill.
- **`task_log_appended` chunk granularity** — line buffered or 1 KB blocks?
  Line is friendlier for `<pre>` rendering and matches `claude -p`'s
  line-oriented stdout. Start with line-buffered; revisit if a task ever
  emits a single >1 MB line.
- **Should the form survive page reloads?** State on the form is ephemeral
  today. Probably keep ephemeral; the user just re-picks. localStorage
  persistence is a v3 if anyone asks.
