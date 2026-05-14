---
noteId: "034f08604f5d11f18eaba108b9c533e7"
tags: []

---

# Terminal sessions

Each session is a **tmux session** wrapped in a **ttyd process** that exposes
it to the browser as an `<iframe>`. resman launches both for you. The
**Ops** tab in the header bar is where the iframes live.

## Spawning a session

The spawn buttons live in the **header bar** (centered between the tab
strip and the connection indicator) and act on the currently selected
vault:

- **+ Claude** — runs `claude --dangerously-skip-permissions` (configurable
  via `app.claude_cmd`) in the vault directory.
- **+ Shell** — drops you into a plain bash session in the vault directory.

Clicking either button **auto-switches the main panel to the Ops tab** so
the new iframe is immediately visible. The iframe loads once ttyd is
accepting connections (resman blocks the API response until then to avoid
the iframe racing ttyd's startup and showing "connection refused").

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
  swaps both the strip and the visible iframe. Iframes for other vaults stay
  in the DOM (so their WebSockets persist) but are hidden.
- Default tab label: `<vault> · <type> · <HH:MM>`, e.g. `vla6 · claude · 08:25`.
- Click anywhere on a tab to switch. Click `×` to kill the ttyd process.
- The `✎` button renames the active tab. Renames live in `localStorage` —
  they are per-browser.

## Closing a tab kills both ttyd and tmux

Clicking the `×` on a tab is treated as an explicit "done with this
terminal" signal — resman tears down both the ttyd process **and** the
underlying tmux session so nothing is left orphaned. If you want a
long-running Claude or shell session to survive across panel restarts,
either keep the tab open or attach to the tmux session from a regular
shell (which will keep it alive even if resman is restarted):

```bash
tmux -L resman ls
tmux -L resman attach -t rsm-vla6-claude-1
```

While that external attach is alive, the tmux session won't die even if
the tab is closed in the browser.

## Reclaiming leftover sessions

If a previous resman run left tmux sessions behind (e.g. after a crash,
or because an external attach kept them alive while the control plane
was restarted), click the **● connected** pill in the top-right of the
header. The sessions-overview modal lists every orphaned tmux session
with a **Kill all** button to clean them up in one shot.

## Obsidian

The **Obsidian** button (in the header, renamed from "Open Obsidian")
launches `app.obsidian_cmd` (e.g. `flatpak run md.obsidian.Obsidian`) with
the vault path appended. Configure it in `resman.yaml`. Resman launches a
detached subprocess and returns immediately.

## ttyd not installed?

The whole panel works without ttyd — the **+ Shell** / **+ Claude** buttons
will just respond `503 Service Unavailable`, and a `ttyd not installed`
note appears in the header next to the spawn buttons.
