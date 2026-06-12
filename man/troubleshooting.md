# Troubleshooting

## "ttyd not installed — terminal sessions disabled"

ttyd is a separate binary. Try:

```bash
sudo snap install ttyd --classic     # Ubuntu / snap
# or grab the prebuilt binary from
# https://github.com/tsl0922/ttyd/releases and drop it on $PATH
./deps.sh --check
```

The rest of the panel works without ttyd.

## "venv at … is broken"

`run.sh` refuses to delete a user-supplied venv that fails its sanity check.
Either:

```bash
rm -rf /path/to/broken-venv
./run.sh --vname /path/to/broken-venv
```

…or pass a different `--vname`.

## Vault dot is red but I can't see why

Click the vault name once to select it, then click the `⚠` icon — the health
modal lists path-exists, `.obsidian/`, wiki home, last session, last task.

If the modal shows everything green but the dot is still red, you have a
**failed** task in history; click the **Tasks** tab to see which one. Tasks
in terminal states older than 90 days are folded into the snapshot during
**Compact log** but their failed status is preserved in the snapshot, so
the dot stays red until you clear the failure manually.

## Schedule says it fired but nothing happened

The cron-skip banner appears **only** if the window was inactive when the
cron fired. Check the **Tasks** tab. If the banner is there, your schedule
is fine — the window is just closed. See [window-state](window-state.md).

If the banner is *not* there and the task didn't run, check the server log
where you started `run.sh` — APScheduler logs every fire.

## "Obsidian" button does nothing

The header **Obsidian** button uses `app.obsidian_cmd` from `resman.yaml`.
If that command doesn't exist on `$PATH` you'll get a 400 with
`obsidian binary not found`.
Common values:

```yaml
obsidian_cmd: "flatpak run md.obsidian.Obsidian"   # flatpak install
obsidian_cmd: "/snap/bin/obsidian"                  # snap install
obsidian_cmd: "obsidian"                            # native install on PATH
```

## Iframe shows "connection refused" right after spawning a session

ttyd takes a moment to start. resman blocks the spawn API until ttyd is
listening, so this should be rare — but on a slow VM it can race.

If it persists, check that ttyd is actually launched:

```bash
ps aux | grep ttyd
ss -lntp | grep 7680
```

If multiple ttyd processes are competing for the same port, kill them and
retry. The port range is configured by `app.ttyd_port_base` /
`app.ttyd_port_max`.

## Help tab says "man/ directory not found"

resman expects `<repo-root>/man/`. If your install
puts it elsewhere, set `app.man_path` in `resman.yaml`:

```yaml
app:
  man_path: /opt/resman-docs
```

Then click ↻ in the Help tab.

## Task is running but its log pane is empty / not updating

Live tailing depends on the child process flushing its stdout. Most CLIs
line-buffer when they detect a TTY and block-buffer (~4 KB) when they
detect a pipe. resman gives every task a **pseudo-terminal** (`pty.openpty`)
specifically to keep CLIs in line-buffered mode — so this should be rare.

If it's still happening:

- Confirm the card's state is actually `running` (yellow border, ▶ icon).
  If it's `completed` the run is already over; click `re-run` and watch
  again.
- Check that the child isn't doing its own internal buffering. For Python
  children, prefer `python -u`. For `claude -p`, the PTY handles it.
- In a container without `/dev/ptmx`, `pty.openpty()` fails and the runner
  falls back to a pipe. A warning is logged at startup. Output will only
  appear in chunks when the child flushes, typically at exit. To regain
  live tailing, ensure the host exposes `/dev/ptmx` (most Docker setups
  do by default; some `lxc` profiles don't).

The full log is always captured to `config/task-logs/<task_id>.log`
regardless of whether live tailing works.

## Output stopped mid-task with "[output capped]"

A single task's log file is capped at 5 MB (`LOG_MAX_BYTES` in
`task_manager.py`). When the cap is hit the runner writes one marker and
discards the rest of the output — this prevents a runaway plugin from
filling your disk or OOMing the browser via Socket.IO. The subprocess keeps
running normally; only the visible/recorded output is dropped.

If you need the full output, raise the cap by editing the constant or wrap
the task to redirect its own output to a file you control.

## Can't cancel a stuck task

The `cancel` button on a `running` card sends `SIGTERM`, waits 5 seconds,
then `SIGKILL`. If the card still says `running` after that:

- A child of the child (e.g., `claude` shelling out to git) may have
  inherited the signal mask and ignored TERM. Find the process tree with
  `ps -ef --forest | grep <pid>` (PID is on the card's expanded view) and
  kill the leaf yourself.
- The task manager's `_finalize` won't run until `proc.wait()` returns, so
  the card stays `running` until the process actually exits.

After manual kill the next replay/load will pick up the dead PID and flip
the state to `interrupted`.

## "Connecting..." spinner that never goes away

Almost always a JavaScript syntax error or a 5xx on the index page —
nothing resman-specific. Open the browser dev tools console and look at the
first red line. The most common cause across edits is a duplicated
`const`/`let` declaration in `app.js`. Reload after fixing.

## Tests fail with import errors

Run pytest with the venv's Python:

```bash
/tmp/resman-venv/bin/python -m pytest
# or
.venv/bin/python -m pytest
```

System Python won't have eventlet / Flask installed.
