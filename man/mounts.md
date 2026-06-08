---
noteId: "a3f2c1804f7111f18eaba108b9c533e7"
tags: []

---

# Bind mounts

Each vault entry in `resman.yaml` accepts an optional `mount:` key. When set,
resman runs `mount --bind <vault.path> <mount>` at startup so the vault's
files appear at the target path on the host — no symlinks required.

```yaml
vaults:
  - name: my-project
    path: /data/projects/my-project
    tags: [research]
    mount: /home/user/my-project   # ← new field
```

## What resman does automatically

| Event | Action |
|-------|--------|
| Server startup | `mount --bind <path> <mount>` for every vault with `mount:` set |
| Config saved (Config tab) | MountManager reconciles: unmounts removed/changed entries, mounts new ones |
| Server shutdown | `umount <mount>` for every mount resman established |

The mount point directory is created automatically if it does not exist.

## Taking a config change to effect

### Option A — Save via the Config tab (no restart needed)
1. Open the **Config** tab in the resman UI.
2. Edit `resman.yaml` to add or modify `mount:` entries.
3. Click **Save**. resman validates the YAML, writes it atomically, and fires
   a `config_reloaded` event. MountManager picks it up immediately:
   - New `mount:` entries are bind-mounted.
   - Removed or changed entries are unmounted first.

### Option B — Restart resman
```bash
# Stop the running server (Ctrl-C or systemctl stop resman)
./run.sh
```
resman re-reads the config from scratch and mounts everything on startup.

### Option C — API (scripting / automation)
```bash
# Mount one vault manually
curl -s -X POST http://localhost:5090/api/vaults/<name>/mount \
     -H "X-Requested-With: resman"

# Unmount one vault
curl -s -X DELETE http://localhost:5090/api/vaults/<name>/mount \
     -H "X-Requested-With: resman"

# Check mount status (inside vault health)
curl -s http://localhost:5090/api/vaults/<name>/health \
  | python3 -m json.tool | grep mount
```

## Privilege requirements

`mount --bind` requires root privileges. Choose one of:

### 1. Run resman as root
```bash
sudo ./run.sh
```
Simple for development machines. Not recommended for shared hosts.

### 2. Passwordless sudoers entry (recommended)
Add a targeted rule that allows only `mount --bind` and `umount`:

```bash
sudo tee /etc/sudoers.d/resman-mounts <<'EOF'
youruser ALL=(ALL) NOPASSWD: /usr/bin/mount --bind * *, /usr/bin/umount *
EOF
sudo chmod 440 /etc/sudoers.d/resman-mounts
```

Replace `youruser` with your username (`echo $USER`). The paths `/usr/bin/mount`
and `/usr/bin/umount` are correct for Debian/Ubuntu/Arch. Verify with
`which mount` if unsure — some older distros put them under `/bin/`.

resman automatically prepends `sudo` to `mount`/`umount` calls when it is
not running as root, so no other changes are needed after adding the rule.

> **Security note:** This grants permission to bind-mount and unmount *any*
> path. It is reasonable on a single-user workstation. On a shared host,
> scope the paths further or use a wrapper script.

### 3. systemd mount units (alternative)
If resman is started via systemd, you can declare the bind-mounts as
`RequiresMountsFor=` dependencies in the service unit so systemd handles
privileges. See the `systemd/` directory in the repo for the service
template.

## Mount status

The startup report always shows mount status:

```
mounts   : 2/2 bound
```

Per-vault mount state is visible in `GET /api/vaults/<name>/health`:

```json
{
  "mount_point": "/home/user/my-project",
  "mount_active": true
}
```

`mount_active: false` with `mount_point` set means the mount failed (check
server logs for the exact error message).

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `mount: failed to bind … Permission denied` | Not running as root, no sudoers rule | See **Privilege requirements** above |
| `mount: command not found` | `mount` not on PATH for the resman user | Install `util-linux` or fix PATH |
| `mount_active: false` after save | Mount failed silently | Check server logs (`journalctl -u resman -n 50`) |
| Mount point not cleaned up after stop | atexit hook did not fire (e.g. SIGKILL) | Run `umount <mount>` manually, or reboot |
