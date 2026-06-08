# Resman — systemd Service Setup

Installs resman as a **systemd user service** so it starts automatically at boot and can be managed without root.

---

## Prerequisites

- Ubuntu 20.04 or later (or any Debian-based distro with systemd ≥ 245)
- Python 3.8+ with venv module (`python3-venv`)
- tmux (mandatory — resman refuses to start without it)
- ttyd (optional — needed for browser terminal sessions)

Install system dependencies first:

```bash
cd /path/to/repo/v1
./deps.sh --vname .vevn22
```

---

## Quick Install

```bash
cd /path/to/repo/systemd
chmod +x install.sh
./install.sh --vname .vevn22
```

The script will:
1. Generate the service file from the template with the correct paths
2. Place it in `~/.config/systemd/user/resman.service`
3. Enable the service (auto-start on boot)
4. Run `loginctl enable-linger` so the service starts at boot even before you log in
5. Start the service immediately

---

## Manual Install

If you prefer to do it step by step:

```bash
# 1. Create the user systemd directory
mkdir -p ~/.config/systemd/user

# 2. Generate the service file (replace paths to match your system)
PROJECT_PATH="/path/to/repo"
VENV_NAME=".vevn22"

sed \
  -e "s|__PROJECT_PATH__|${PROJECT_PATH}|g" \
  -e "s|__VENV_NAME__|${VENV_NAME}|g" \
  resman.service.template > ~/.config/systemd/user/resman.service

# 3. Reload systemd and enable the service
systemctl --user daemon-reload
systemctl --user enable resman

# 4. Allow the service to start at boot without an active login session
sudo loginctl enable-linger $USER

# 5. Start it now
systemctl --user start resman
```

---

## Managing the Service

```bash
systemctl --user status  resman       # check if running
systemctl --user start   resman       # start
systemctl --user stop    resman       # stop
systemctl --user restart resman       # restart
journalctl --user -u resman -f        # follow live logs
journalctl --user -u resman --since today   # logs from today
```

---

## Uninstall

```bash
./install.sh --uninstall
```

Or manually:

```bash
systemctl --user stop resman
systemctl --user disable resman
rm ~/.config/systemd/user/resman.service
systemctl --user daemon-reload
```

---

## Files in This Directory

| File | Purpose |
|------|---------|
| `resman.service.template` | Service file template with `__PROJECT_PATH__` and `__VENV_NAME__` placeholders |
| `install.sh` | Install/uninstall script — fills placeholders, registers, and starts the service |
| `README.md` | This file |

---

## Troubleshooting

**Service fails to start — "No resman.yaml found"**

Copy the example config and edit it before starting:
```bash
cp /path/to/repo/v1/config/resman.yaml.example /path/to/repo/v1/config/resman.yaml
# or place a per-user override:
cp /path/to/repo/v1/config/resman.yaml.example ~/.resman.yaml
```

**Service fails to start — venv errors**

Re-run deps.sh to recreate the venv:
```bash
cd /path/to/repo/v1
./deps.sh --vname .vevn22
```

**`loginctl enable-linger` requires sudo**

On some Ubuntu setups linger requires elevated privileges:
```bash
sudo loginctl enable-linger $USER
```

**Port 5090 already in use**

Edit `~/.resman.yaml` or `config/resman.yaml` and change `app.port`, then restart the service.

**Check what the service is actually running**

```bash
systemctl --user cat resman
```
