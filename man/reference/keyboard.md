# Keyboard reference

resman's UI is mostly mouse-driven, but a handful of shortcuts are useful.

## Inside terminals

The terminal iframe is **ttyd**, which is plain xterm.js. Standard terminal
keystrokes apply. tmux-specific:

| Combo | Effect |
|-------|--------|
| `Ctrl-b d` | Detach from the tmux session — ttyd will exit, but the tmux session stays alive on the resman socket |
| `Ctrl-b [` | Enter copy mode (scrollback) |
| `Shift-PgUp` / `Shift-PgDn` | Browser-side scrollback (xterm.js buffer) |

## Tabs

The terminal tab strip currently has **no keyboard shortcuts** for switching
tabs (Phase 6 idea: ⌥1 / ⌥2 …). Click a tab or use the rename (pencil)
button in the title bar.

## Activity bar

The views — **Home**, **Wiki**, **Inbox**, **Ops**, **Tasks**, **Windows**,
**Help**, **Config** — are the icons on the activity bar (left edge) and can
be clicked. There are no global keystroke bindings yet.

## Why so few shortcuts?

resman is intentionally a "click around and read state" panel. The high-value
keystrokes happen *inside* terminals, where Claude (or your shell) has the
real shortcuts. If you find yourself reaching for a missing shortcut at the
panel level, file the request — it's a small change.
