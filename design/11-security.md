# Security

## Overview

resman is a local-only tool; its threat model is confined to preventing accidents and
protecting the user from their own config inputs, not from external attackers. The
primary risks are: shell injection via task params, path traversal via file-serving
endpoints, and HTML injection in the SPA. All subprocess calls use the argument-list
form of `subprocess.run()` — the OS shell is never invoked. Input validation happens
at the boundary where user-supplied data enters the system.

## Subprocess Safety

All task execution and tmux commands are constructed as **argument lists** — never as
strings passed to `sh -c` or `subprocess.run(..., shell=True)`. This eliminates shell
metacharacter expansion and injection from `params` fields.

The `run-shell` operation is the most powerful — it runs an arbitrary program in the vault
directory. It is treated as a privileged operation:
- UI displays a warning icon on run-shell tasks
- Requires explicit user acknowledgment (modal confirmation) before the first use per session
- Still runs as an argument list (`execvp` semantics); no shell metacharacter expansion
- Does not prevent a determined user from running any program — the safety guarantee is
  no shell string interpolation, not no code execution

## Input Validation

| Input | Validation |
|-------|-----------|
| Vault names, task names | `[a-zA-Z0-9_-]` only; enforced at creation |
| `params.url` | Must parse as HTTP or HTTPS via `urllib.parse.urlparse()`; non-http schemes rejected |
| `params.topic`, `params.prompt` | Max 200 characters; printable ASCII only |
| `params.cmd_parts` | Must be a pre-parsed list; individual elements are not re-parsed as shell |
| YAML config content | `yaml.safe_load()` only; result must be a dict; validated before write |
| File size (config saves) | Reject content exceeding 1 MB |

## Path Traversal Prevention

All file-serving endpoints normalize the requested path with `os.path.normpath()` and
verify it begins with the allowed root directory via `startswith(allowed_root)`. Requests
that resolve outside the allowed root are rejected with HTTP 403.

`scan_paths`: entries are validated to ensure they do not resolve to filesystem roots
(`/`, `/home`, etc.); max scan depth is 2 levels.

## CSRF Mitigation

All mutating REST endpoints require the header `X-Requested-With: resman`. Requests
without this header are rejected with HTTP 403 before any state mutation. This is
sufficient for a localhost-only tool — cross-site requests from `file://` or external
origins cannot include arbitrary custom headers. The SPA applies the header via a
shared fetch wrapper. Socket.IO handlers verify the header on connection.

## HTML Injection

All user-controlled values (vault names, task names, params) are passed through `esc()`
before being inserted into the DOM. Template literals with raw user data are prohibited.

## tmux Isolation

resman uses an isolated tmux socket (`resman`) and a dedicated session name prefix (`rsm-`).
It never interacts with the user's personal tmux sessions.

## Obsidian Push Writes

Writes to `_resman/status.md` are wrapped in `try/except OSError`. A write failure is
logged as a warning and skipped; it never propagates to crash the server.

## Key Decisions

- **Argument-list subprocess calls only** — shell string execution is prohibited for all task types including run-shell
- **run-shell requires acknowledgment** — one-time per session; surfaced in UI with warning icon
- **CSRF header sufficient** — no CSRF token storage needed for localhost-only tool
- **YAML: safe_load only** — `yaml.load()` with arbitrary loader is never used

## Constraints

- `shell=True` and `sh -c` are prohibited in all subprocess calls
- `yaml.safe_load()` is the only permitted YAML loading function
- Every mutating endpoint must check `X-Requested-With: resman` before processing
- Every user-controlled DOM value must pass through `esc()`
- Config writes must be rejected if file size exceeds 1 MB

## Open Questions

- None
