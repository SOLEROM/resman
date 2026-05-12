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

## Resman never kills your tmux sessions

Closing a tab terminates the ttyd process, but the underlying tmux session
**stays alive**. You can reattach manually:

```bash
tmux -L resman ls
tmux -L resman attach -t rsm-vla6-claude-1
```

This is intentional — long-running Claude sessions survive a panel restart.

## Obsidian

The **Obsidian** button (in the header, renamed from "Open Obsidian")
launches `app.obsidian_cmd` (e.g. `flatpak run md.obsidian.Obsidian`) with
the vault path appended. Configure it in `resman.yaml`. Resman launches a
detached subprocess and returns immediately.

## ttyd not installed?

The whole panel works without ttyd — the **+ Shell** / **+ Claude** buttons
will just respond `503 Service Unavailable`, and a `ttyd not installed`
note appears in the header next to the spawn buttons.
