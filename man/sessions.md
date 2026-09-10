---
noteId: "034f08604f5d11f18eaba108b9c533e7"
tags: []

---

# Terminal sessions

Each session is a **tmux session** streamed to the browser by the shared
**webterm** library (`solBench/webterm`) over the page's own WebSocket.
resman's own process holds the pty, so there is no second HTTP server, no
port pool and no iframe. The **Ops** view (terminal icon in the activity
bar) is where the terminals live.

`RESMAN_WEBTERM=0` reverts to the legacy stack: one **ttyd** process per
session on its own port, embedded as an `<iframe>`. Both stacks drive the
*same* tmux socket and prefix, so sessions survive switching between them.

## Spawning a session

The spawn buttons live in the **title bar** (centered, next to the active
vault's name) and act on the currently selected vault:

- **+ Claude** — runs `claude --dangerously-skip-permissions` (configurable
  via `app.claude_cmd`) in the vault directory.
- **+ Shell** — drops you into a plain bash session in the vault directory.

Clicking either button **auto-switches the main panel to the Ops tab** so
the new terminal is immediately visible.

A **+ Claude** session started with a slash command (the new-vault wizard
does this) does not guess when the REPL is ready: webterm watches the pane
for Claude's input box and only then pastes and submits. That matters here
because the delivery ends in Enter — under the old fixed delay a slow REPL
meant the text was typed and *run* in the bash shell that briefly precedes
Claude.

## Returning to your sessions

Two equivalent shortcuts open the Ops tab for the active vault:

- Click the **Ops** tab in the header.
- Click the **vault-name label** in the header (e.g. `val6`) — it doubles
  as a one-click way back to the terminal view when you're reading wiki
  content.

Each vault remembers its own last-seen panel, so hopping between vaults
restores each one's own view (Wiki / Ops / Tasks / Config).

## The tab strip

- The strip is **filtered to the currently selected vault** — switching vaults
  swaps which tabs are shown. Terminals for other vaults stay attached in the
  background (their output keeps streaming) but their tabs are hidden.
- Default tab label: `<vault> · <type>`.
- Click anywhere on a tab to switch. Click `×` to kill the session.
- The `✎` button renames the active tab. Renames live in `localStorage` —
  they are per-browser.

## Closing a tab kills the tmux session

Clicking the `×` on a tab is treated as an explicit "done with this
terminal" signal — resman tears down the underlying tmux session so nothing
is left orphaned. If you want a
long-running Claude or shell session to survive across panel restarts,
either keep the tab open or attach to the tmux session from a regular
shell (which will keep it alive even if resman is restarted):

```bash
tmux -L resman ls
tmux -L resman attach -t rsm-vla6-claude-1
```

While that external attach is alive, the tmux session won't die even if
the tab is closed in the browser.

## Leftover sessions from a previous run

There are none to reclaim any more. tmux is webterm's source of truth, so a
session left behind by a crash or a restart is simply picked up as a tab the
next time the page loads — the terminal and its scrollback are still there.

That is also why the old **Kill all orphans** action is gone: under the
shared terminal every tmux session on resman's socket *is* a live tab, so
"kill everything I'm not tracking" would have killed all of them. The
endpoint refuses with a 409 saying so. Close the tabs you don't want.

Under `RESMAN_WEBTERM=0` the old behaviour returns, because the legacy stack
really can leave ttyd-less tmux sessions behind.

## Obsidian

The **Obsidian** button (in the header, renamed from "Open Obsidian")
launches `app.obsidian_cmd` (e.g. `flatpak run md.obsidian.Obsidian`) with
the vault path appended. Configure it in `resman.yaml`. Resman launches a
detached subprocess and returns immediately.

## ttyd not installed? (legacy stack only)

The default terminal needs no ttyd at all. Under `RESMAN_WEBTERM=0` the
whole panel works without it — the **+ Shell** / **+ Claude** buttons
will just respond `503 Service Unavailable`, and a `ttyd not installed`
note appears in the header next to the spawn buttons.
