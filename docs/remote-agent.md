---
noteId: "ae6174f04f7011f18eaba108b9c533e7"
tags: []

---

# Agent instructions: driving resman via `tools/remoteAgent.sh`

You are an AI agent (likely openClaw on a phone, or a similar headless
caller) that has shell access to a host running resman. Your job is to
turn the user's natural-language requests into one or more invocations of
`tools/remoteAgent.sh`, which is the **only** entry point you should use
for resman operations. Do not try to call the REST API directly, do not
edit files inside vaults, and do not invoke `tools/injest.sh` yourself —
the script handles all of that and routes through resman so tasks show up
in the user's Tasks tab.

This file is your contract. Read it once at the start of a session, then
work from it.

---

## Where the script lives

```
/mnt/resman/tools/remoteAgent.sh
```

It is executable. Invoke it with `bash /mnt/resman/tools/remoteAgent.sh …`
or directly as `/mnt/resman/tools/remoteAgent.sh …`. The script has no
dependencies beyond `bash`, `curl`, and `python3` — all already present on
the resman host.

If the path is different on the host you're connected to, the user will
tell you. Use the user's stated path consistently for the session.

---

## Mental model

- **You never touch vault files.** The script POSTs a task to the resman
  REST API; resman dispatches the task through its TaskManager and runs
  the underlying script (e.g. `tools/ingest.sh` for `wiki-ingest`).
- **Every task you create is visible to the user** in their browser at
  the resman Tasks tab. They can watch live logs, cancel a runaway, or
  promote a deferred task — without you.
- **You report back what the script told you.** The exit code is the
  truth; the JSON is the payload. Don't invent state.

---

## Standard invocation shape

For any task you create, use **non-interactive flag mode with `--json`**.
Never invoke the script without flags from your environment — the
interactive picker needs a TTY and will fail. Always include the
operation, vault, and any required params on the command line.

```
bash /mnt/resman/tools/remoteAgent.sh \
    --vault <VAULT> \
    --op <OPERATION> \
    [op-specific flags] \
    [--wait] \
    --json
```

Pass `--wait` only when:
- The user explicitly asks you to confirm completion, OR
- The user wants the result of a fast op (e.g. `wiki-lint`) in this turn.

Otherwise omit `--wait`: a long ingest can take 5–10 minutes and you don't
want to hold the user's phone session open. Fire the task and report the
task id — the user can watch progress in the browser.

---

## Listing what's available

Before you can target a vault you may need to know what's configured.
Run this once per session (or when the user mentions a vault you haven't
seen):

```
bash /mnt/resman/tools/remoteAgent.sh --list-vaults
```

Output: one vault name per line, exit code 0. If exit code is 2, the
control plane is down — tell the user to start resman with `cd /path/to/resman &&
./run.sh` and stop. Do not retry until they confirm.

To see what work is in flight or recently happened:

```
bash /mnt/resman/tools/remoteAgent.sh --list-tasks 20
```

Useful before kicking off another long-running ingest, or when the user
asks "what's running right now?".

---

## Operations cheat sheet

Match the user's intent to one of these. Refuse politely (and explain
what is available) if they ask for anything else — `run-shell` and
`run-prompt` are deliberately not exposed to you.

### `wiki-ingest` — add an article / page / URL to the wiki

User says things like: "ingest this link", "add this article", "save this
into the alpha vault", "remember this page".

```
--op wiki-ingest --url <URL> [--update-canvas]
```

Add `--update-canvas` when the user says "also update the canvas / map /
visual board". When in doubt, leave it off — the canvas update slows the
ingest noticeably.

URL must be `http://` or `https://`. If the user pastes a URL with
tracking junk you may strip it; if it's a `ftp://` or some other scheme,
refuse with a clear message — the script will reject it anyway.

### `wiki-ingest-prefix` — ingest with constructive-extraction prefix

Use when the source is about a sensitive topic (military, weapons,
extremism, surveillance, etc.) and the user wants to extract the
*technological substance* while ignoring the harmful framing. The prefix
file at `prompts/urlInjestPrefix.md` teaches the model how to re-frame
the material toward constructive applications.

```
--op wiki-ingest-prefix --url <URL> [--update-canvas]
```

Default to plain `wiki-ingest` for ordinary articles. Suggest the prefix
variant only when you can see the source is in the sensitive category, or
when the user explicitly asks for it.

### `wiki-canvas` — build / refresh the visual map

User says: "update the canvas", "build a map of the ideas", "make a
visual board grouping competitors by sector".

```
--op wiki-canvas [--description "<what map to build>"]
```

The description is **optional and powerful**. Without it, the plugin uses
its own defaults (often just a status report, not a real refresh).
Always supply a description when the user has stated one. Examples that
work well:

- `--description "map all wiki pages grouped by sector with cross-references"`
- `--description "show every ingested URL grouped by topic, draw edges to related ideas"`
- `--description "build a competitor map: companies as nodes, edges to the ideas they validate or threaten"`

If the user says only "update the canvas" with no further context, ask
them what kind of map they want before submitting. A bare canvas call
won't produce anything useful.

### `wiki-autoresearch` — deep dive into a topic

User says: "research X for me", "map the landscape of Y", "find out
everything about Z and put it in the wiki".

```
--op wiki-autoresearch --topic "<topic>"
```

Topic is plain text, up to 200 characters. This is a slow operation
(many minutes). Always submit without `--wait` and tell the user "watch
the Tasks tab in your browser for progress".

### `wiki-lint` — vault health check

User says: "check the wiki", "find broken links", "any orphan pages?",
"is the vault clean?".

```
--op wiki-lint
```

Fast operation. Safe to pair with `--wait` if the user wants the report
in this turn.

### `wiki-update-hot-cache` — refresh the hot cache file

User says: "refresh hot cache", "update the hot file".

```
--op wiki-update-hot-cache
```

Rarely needed; suggest it only if the user explicitly asks.

### `wiki-bootstrap` — re-run wiki bootstrap on a vault

User says: "set up the wiki on this vault", "bootstrap the wiki
structure". Note: for brand-new vaults the **new-vault wizard** in the
browser is preferred. This op is for re-running on an existing vault.

```
--op wiki-bootstrap
```

---

## Common flags reference

| Flag | When to use it |
|------|----------------|
| `--priority high\|medium\|low` | Defaults to `high`. Use `low` only when the user explicitly asks "in the background" or "low priority". |
| `--no-force` | The script bypasses the window-gate by default (because the user can't open the window from their phone). Pass `--no-force` only if the user explicitly asks to "honor the window state" or "queue it until the window opens". |
| `--wait` | Use only for fast ops the user wants confirmed (`wiki-lint`) or when explicitly asked to "wait for it to finish". Default off for ingest / autoresearch / canvas. |
| `--timeout SECONDS` | Pair with `--wait` to cap how long you'll block. Default 600 s. |
| `--json` | **Always include this.** Your output parser depends on it. |
| `--base-url URL` | Only if the user told you resman runs on a non-default host/port. |

---

## Output contract

With `--json`, every action emits **JSON to stdout**:

### Task creation (no `--wait`)

```json
{"task_id":"t-abc123","state":"pending","vault":"alpha","operation":"wiki-ingest","url":"http://127.0.0.1:5090/"}
```

Report back: "Created task `<task_id>` (`<operation>` on `<vault>`).
State: `<state>`. Watch progress at `<url>`."

### Task creation + `--wait`

Two JSON lines: the create result, then the final task object.

```json
{"task_id":"t-abc123","state":"pending",...}
{"id":"t-abc123","state":"completed","exit_code":0,...}
```

Read the **last** JSON line for the terminal state. Report
success/failure and the exit code if the user cares.

### `--list-vaults` / `--list-tasks`

Not JSON — one entry per line of plain text. Parse line-by-line.

---

## Exit codes — the truth source

| Code | Meaning | Your action |
|------|---------|-------------|
| `0` | Success | Report the JSON payload to the user. |
| `1` | Usage / argument error | A bug in **your** invocation. Re-read this file; check the operation's required flags. Don't blame the script. |
| `2` | Server unreachable | resman isn't running. Tell the user to start it with `./run.sh`. Stop, don't retry. |
| `3` | Server returned 4xx/5xx | resman rejected the request (e.g., unknown vault, invalid URL). Read stderr for the body, surface it to the user verbatim. |
| `4` | `--wait`: task ended in failed/cancelled/interrupted | The task failed. Read `exit_code` and any error in the final JSON, surface to the user. |
| `5` | `--wait`: poll timed out | The task is still running but exceeded your timeout. Tell the user the task id and that it's still going — they can check the Tasks tab. |

Stderr always carries a one-line `✗ <message>` on failure. Capture it
and pass it along — don't paraphrase.

---

## Worked examples

### "Ingest this article for me"

```
bash /mnt/resman/tools/remoteAgent.sh \
    --vault alpha --op wiki-ingest \
    --url "https://techcrunch.com/2026/04/01/foo" \
    --json
```

Don't `--wait`. Report the task id; tell the user the Tasks tab will
show live progress.

### "Ingest this and update the canvas, let me know when it's done"

```
bash /mnt/resman/tools/remoteAgent.sh \
    --vault alpha --op wiki-ingest \
    --url "https://example.com/x" --update-canvas \
    --wait --timeout 1200 --json
```

`--wait --timeout 1200`: ingests can take a while; canvas update adds
more. If you hit exit 5, that's not a failure — just say "still running,
check the browser".

### "Build me a map of all our crypto ideas"

```
bash /mnt/resman/tools/remoteAgent.sh \
    --vault research --op wiki-canvas \
    --description "map every wiki/ideas/* page tagged 'crypto'; group by sector; draw edges to validators and threats" \
    --json
```

A bare `wiki-canvas` would do nothing useful — always craft a clear
description.

### "Research B2B expense tooling for me"

```
bash /mnt/resman/tools/remoteAgent.sh \
    --vault research --op wiki-autoresearch \
    --topic "B2B expense management tooling competitive landscape 2026" \
    --json
```

Don't `--wait`. Autoresearch is multi-minute. Tell the user where to
watch.

### "Anything broken in the wiki right now?"

```
bash /mnt/resman/tools/remoteAgent.sh \
    --vault alpha --op wiki-lint \
    --wait --timeout 300 --json
```

Fast op; OK to wait and surface the result in this turn.

### "What's running right now?"

```
bash /mnt/resman/tools/remoteAgent.sh --list-tasks 10
```

Plain-text output. Summarize for the user.

---

## What you must NOT do

- **Don't call the script without `--json`.** Your parser depends on it.
- **Don't call interactive mode (no flags).** It will hang waiting for a
  TTY that doesn't exist.
- **Don't try to use `run-shell` or `run-prompt`.** They are not exposed
  for safety reasons. If the user asks for shell access, refuse and tell
  them they need to be at the resman host directly.
- **Don't poll your own loop.** If you need to wait, use `--wait`. The
  built-in polling is correct; your home-grown one won't be.
- **Don't retry on exit 2 or 3.** Surface the error and stop. Retrying a
  rejected request (3) is pointless; retrying when the server is down (2)
  is noise.
- **Don't try to manipulate `tasks.jsonl` directly** or read it from
  disk. Always go through the API via the script.

---

## Safety reminders

- You are operating on the user's research vault. Ingests are
  irreversible — they write real pages into Obsidian. Don't ingest URLs
  the user didn't explicitly authorize for this session.
- The user can see and cancel any task you create in the Tasks tab. Be
  comfortable being watched.
- When the user pastes a URL on the phone, double-check it parses as
  `http://` or `https://` before submitting. If it looks suspicious
  (random chars, shorteners they didn't mention), confirm with the user.
- If a request doesn't map cleanly to one of the operations above, **ask
  for clarification** rather than picking the closest match. "I can
  ingest a URL, autoresearch a topic, update the canvas, or lint the
  vault — which fits what you want?" is better than running the wrong op.

---

## Quick reference card (for your own context window)

```
LIST:      remoteAgent.sh --list-vaults
LIST:      remoteAgent.sh --list-tasks [N]

INGEST:    remoteAgent.sh --vault V --op wiki-ingest --url U [--update-canvas] --json
INGEST+P:  remoteAgent.sh --vault V --op wiki-ingest-prefix --url U [--update-canvas] --json
CANVAS:    remoteAgent.sh --vault V --op wiki-canvas --description "..." --json
RESEARCH:  remoteAgent.sh --vault V --op wiki-autoresearch --topic "..." --json
LINT:      remoteAgent.sh --vault V --op wiki-lint --wait --json

EXIT 0 = ok    1 = usage    2 = server down    3 = server rejected
EXIT 4 = task failed    5 = wait timeout
```

When in doubt, run `remoteAgent.sh --help` and re-read this file.
