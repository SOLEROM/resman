# Terminal sessions

Each session is a **tmux session** wrapped in a **ttyd process** that exposes
it to the browser as an `<iframe>`. resman launches both for you.

## Spawning a session

In the **Terminal** view (the default tab), with a vault selected:

- **+ Claude** — runs `claude --dangerously-skip-permissions` (configurable
  via `app.claude_cmd`) in the vault directory.
- **+ Shell** — drops you into a plain bash session in the vault directory.

The new tab appears at the top of the terminal frame, and the iframe loads
once ttyd is accepting connections (resman blocks the API response until then
to avoid the iframe racing ttyd's startup and showing "connection refused").

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

## Open Obsidian

The **Open Obsidian** button launches `app.obsidian_cmd` (e.g.
`flatpak run md.obsidian.Obsidian`) with the vault path appended. Configure it
in `system.yaml`. Resman launches a detached subprocess and returns
immediately.

## ttyd not installed?

The whole panel works without ttyd — the **+ Shell** / **+ Claude** buttons
will just respond `503 Service Unavailable`, and the tab strip shows
"ttyd not installed — terminal sessions disabled".
