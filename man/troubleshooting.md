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

## "Open Obsidian" button does nothing

The button uses `app.obsidian_cmd` from `system.yaml`. If that command
doesn't exist on `$PATH` you'll get a 400 with `obsidian binary not found`.
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

resman expects `<repo-root>/man/` — the sibling of `v1/`. If your install
puts it elsewhere, set `app.man_path` in `system.yaml`:

```yaml
app:
  man_path: /opt/resman-docs
```

Then click ↻ in the Help tab.

## Tests fail with import errors

Run pytest with the venv's Python:

```bash
/tmp/resman-venv/bin/python -m pytest
# or
.venv/bin/python -m pytest
```

System Python won't have eventlet / Flask installed.
