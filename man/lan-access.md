# LAN access (`--public`)

By default resman binds Flask **and** ttyd to `127.0.0.1` — accessible only
from the machine running it. The `--public` flag opens both up to the LAN.

```bash
./run.sh --public
```

The startup banner shows the discovered LAN IP and a `[PUBLIC]` warning:

```
server   : http://0.0.0.0:5090  (LAN: http://192.168.2.115:5090)  [PUBLIC — exposed on local network]
```

## What `--public` actually changes

| Subsystem | Default | `--public` |
|-----------|---------|------------|
| Flask bind | `127.0.0.1:5090` | `0.0.0.0:5090` |
| Socket.IO CORS | only the loopback origin | `*` (any origin) |
| ttyd bind | `127.0.0.1:<7680..7999>` | `0.0.0.0:<7680..7999>` |

The browser builds the iframe URL from `window.location.hostname` + the
ttyd port — so if you load `http://192.168.x.y:5090`, the iframe goes to
`http://192.168.x.y:7680`. No client-side configuration needed.

## `--host` (alternative)

If you want to bind Flask without relaxing CORS or opening ttyd, pass an
explicit interface:

```bash
./run.sh --host 0.0.0.0
```

This is rarely useful — your browser will still hit ttyd via 127.0.0.1, which
won't work from another host. Prefer `--public` for the typical "show this to
my laptop" use case.

## Security reality check

resman has **no authentication**. The CSRF header (`X-Requested-With: resman`)
just stops cross-origin form submissions; it does not stop another machine on
your LAN from talking to the API directly.

When you run `--public`:

- Anyone on your LAN can spawn Claude / shell sessions in any registered
  vault.
- They can read any file under any vault path (via the wiki/health endpoints).
- They can edit `resman.yaml` and `schedule.yaml` via the Config tab.

Use it on a trusted home network, not a coffee-shop wifi. If you need a more
robust setup, put resman behind a reverse proxy with HTTP basic auth.

## Firewall

Make sure your host firewall lets the LAN reach **5090** *and* the **ttyd
port range** (default 7680–7999). On Ubuntu / GNOME:

```bash
sudo ufw allow from 192.168.0.0/16 to any port 5090
sudo ufw allow from 192.168.0.0/16 to any port 7680:7999 proto tcp
```
