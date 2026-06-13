# Activity log

The **📋 Log** button at the **lower-right of the footer** opens the activity
log — a live view of what resman is doing right now.

It is **volatile**: it exists only while the server is running. Entries are kept
in memory and mirrored to a throwaway file under `/tmp/resman/`; when you stop
resman the log is gone. It is a "what's happening" view, not a history you can
go back to days later.

## The window

- **Rows** show the time, level, source, and message — newest at the bottom.
- **Levels** are colour-coded: info (blue tag), **warn** (amber), **error**
  (red). A warning or error that arrives while the window is *closed* lights a
  small dot on the Log button so you notice it.
- **level filter** — show all, info+, warn+, or only errors.
- **Clear** — empties the log.
- The window **updates live** — new entries stream in as they happen, and it
  keeps scrolling to the newest as long as you're already at the bottom.

## What gets logged

- **Window limit sync** — every time the footer **⟳** runs (on load, every few
  minutes, or when you click it) you'll see it start and its result, e.g.
  `window limit sync ok — session 8%, weekly 9%`, or a warning if you're logged
  out / it couldn't reach claude.ai. This is the quickest way to confirm the
  sync is working and to see errors.
- **Tasks** — queued, running, completed, failed, cancelled.
- **Sessions** — spawned, crashed, orphans killed.
- **Vaults** — registered, scaffolded (and failures).
- **Config** — saved (and validation failures).
- **Errors** — unexpected warnings/errors from anywhere in the server surface
  here too.

If something seems stuck or silently failing, open the Log window first.
