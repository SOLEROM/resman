# LAN access (`--public`)

By default resman binds Flask to `127.0.0.1` — accessible only from the
machine running it. The `--public` flag opens the app up to the LAN.

**The terminal has a gate of its own**, and `--public` does not open it. See
[Who may reach the terminal](#who-may-reach-the-terminal) below.

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
| Terminal | loopback + tailnet | loopback + tailnet (unchanged) |
| ttyd bind (legacy stack only) | `127.0.0.1:<7680..7999>` | `0.0.0.0:<7680..7999>` |

The terminal rides resman's own port now — there is no second port to open,
and no iframe URL to rebuild.

## Who may reach the terminal

`--public` exposes the *app*; it deliberately does not hand out shells with
it. Every webterm endpoint and its Socket.IO handshake pass through the
library's own check, which by default accepts **loopback plus the tailnet**
and nothing else — the LAN the box also sits on is excluded on purpose. So
under `--public` the UI is reachable from a phone on the LAN while the
terminal is not, unless you say otherwise:

| Variable | Effect |
|---|---|
| `RESMAN_WEBTERM_TRUSTED_NETS="cidr,cidr"` | Replace the trusted set. Empty value means loopback-only. |
| `RESMAN_WEBTERM_LAN=1` | Accept every peer. The name is the warning. |
| `RESMAN_WEBTERM=0` | Revert to the legacy ttyd terminal entirely. |

An unparseable CIDR is dropped rather than widened, so a typo fails closed.

## `--host` (alternative)

If you want to bind Flask without relaxing CORS, pass an explicit interface:

```bash
./run.sh --host 0.0.0.0
```

This is rarely useful under the legacy stack — your browser will still hit
ttyd via 127.0.0.1, which
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

Make sure your host firewall lets the LAN reach **5090** (and, on the legacy
stack only, the **ttyd
port range** (default 7680–7999). On Ubuntu / GNOME:

```bash
sudo ufw allow from 192.168.0.0/16 to any port 5090
sudo ufw allow from 192.168.0.0/16 to any port 7680:7999 proto tcp
```
