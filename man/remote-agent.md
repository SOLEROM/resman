---
noteId: "82c741a04f5f11f18eaba108b9c533e7"
tags:
  - "reference"
  - "automation"

---

# Remote agent (`tools/remoteAgent.sh`)

`tools/remoteAgent.sh` is a CLI bridge to the running resman control plane.
It exists so an unattended caller — a script, a cron job, or a remote agent
like **openClaw on your phone over SSH** — can drive resman without using
the browser UI. Tasks it creates go through `POST /api/tasks`, so they
appear in the **Tasks** tab automatically alongside browser-triggered work.

The script never calls `tools/injest.sh` (or any operation script) directly.
It always routes through the API so window-gating, validation, live log
streaming, and Tasks-tab visibility all keep working.

---

## Two ways to use it

**1. At a terminal — interactive picker.**

```bash
./tools/remoteAgent.sh
```

You'll get a numbered vault list, then a numbered operation list, then any
operation-specific prompts (URL, topic, etc). Useful for ad-hoc work when
you're SSH'd into the host and don't want to open a browser.

**2. From a remote agent over SSH — non-interactive flags.**

```bash
ssh user@host '/mnt/resman/tools/remoteAgent.sh \
    --vault alpha --op wiki-ingest \
    --url https://example.com/article --update-canvas \
    --wait --json'
```

`--json` emits a single JSON line on stdout per action, so the calling
agent can parse it cleanly. `--wait` polls until the task hits a terminal
state and reports success/failure via the exit code.

---

## Operations exposed

Deliberately a safe subset — `run-shell` and `run-prompt` are **not**
exposed by this script. Editing the script's `ALLOWED_OPS` array is the
opt-in if you decide you want them.

| Op | Required flags | Optional |
|----|----------------|----------|
| `wiki-ingest` | `--url` | `--update-canvas` |
| `wiki-ingest-prefix` | `--url` | `--update-canvas` |
| `wiki-canvas` | (none) | `--description` |
| `wiki-lint` | (none) | |
| `wiki-autoresearch` | `--topic` | |
| `wiki-update-hot-cache` | (none) | |
| `wiki-bootstrap` | (none) | |

---

## Common flags

| Flag | Default | Notes |
|------|---------|-------|
| `--priority {high\|medium\|low}` | `high` | Mirrors the trigger-form default |
| `--no-force` | force is **on** | Window-gating is bypassed by default — a phone-driven user can't open the window manually. Pass `--no-force` to honor the window state |
| `--wait` | off | Poll `GET /api/tasks/{id}` until terminal state |
| `--timeout SECONDS` | 600 | Max wait time |
| `--base-url URL` | from `resman.yaml` | Override the resman server location |
| `--json` | off | Single JSON line per action — for openClaw / other parsers |
| `--quiet` | off | Errors only |

The base URL defaults to `http://<app.host>:<app.port>` from the live
resman.yaml (so it honors the `~/.resman.yaml` per-user override). Falls
back to `http://127.0.0.1:5090`. Override with `--base-url` or
`$RESMAN_BASE_URL`.

---

## Listing helpers

```bash
./tools/remoteAgent.sh --list-vaults
./tools/remoteAgent.sh --list-tasks         # 10 most recent
./tools/remoteAgent.sh --list-tasks 50      # 50 most recent
```

`--list-vaults` tries the API first; if the server is down, it falls back
to parsing resman.yaml directly so you can still see what's configured.

---

## Exit codes

The exit code is the contract for any agent calling this script. Treat
them as machine-readable signals:

| Code | Meaning |
|------|---------|
| `0` | Success (task created, or `--wait` returned `completed`) |
| `1` | Usage / argument error |
| `2` | Server unreachable (transport failure) |
| `3` | Server returned 4xx / 5xx |
| `4` | `--wait`: task ended in `failed` / `cancelled` / `interrupted` |
| `5` | `--wait`: poll timed out |

---

## openClaw integration sketch

The intended phone-driven flow:

1. Open openClaw on the phone, ask it to "ingest <URL> into the alpha vault".
2. openClaw shells in over SSH and invokes the script:
   ```bash
   ssh resman-host '/mnt/resman/tools/remoteAgent.sh \
       --vault alpha --op wiki-ingest --url "$URL" \
       --update-canvas --wait --json'
   ```
3. openClaw parses the JSON line(s) and reports back to you on the phone.
4. Meanwhile, opening the resman browser UI shows the same task running
   in the **Tasks** tab — you can watch the live log there if you want.

---

## Examples

Fire a URL ingest and walk away:

```bash
./tools/remoteAgent.sh --vault alpha --op wiki-ingest \
    --url https://techcrunch.com/some-article
```

Ingest + canvas update + wait for completion + machine-readable output:

```bash
./tools/remoteAgent.sh --vault alpha --op wiki-ingest \
    --url https://example.com/x --update-canvas --wait --json
```

Run autoresearch on a topic at low priority and don't bypass the window:

```bash
./tools/remoteAgent.sh --vault research --op wiki-autoresearch \
    --topic "edge compute landscape 2026" \
    --priority low --no-force
```

Update the canvas with a custom description:

```bash
./tools/remoteAgent.sh --vault alpha --op wiki-canvas \
    --description "map all ideas grouped by sector with cross-references"
```

Lint every Friday from cron:

```cron
0 9 * * 5  /mnt/resman/tools/remoteAgent.sh --vault alpha --op wiki-lint --quiet
```

---

## Troubleshooting

**`✗ cannot reach resman at http://127.0.0.1:5090`**
The control plane isn't running. Start it with `cd v1 && ./run.sh`. If
resman is on a different host or port, pass `--base-url`.

**`✗ server returned HTTP 400: {"error":"vault 'X' is not registered"}`**
Spelling — run `./tools/remoteAgent.sh --list-vaults` to see what resman
sees.

**`✗ --url required for wiki-ingest`**
Non-interactive flag mode (or no TTY) needs every required param up
front. The script never falls back to prompting when stdin isn't a
terminal, so an SSH-from-phone call gets a clean error instead of
hanging on a `read`.
