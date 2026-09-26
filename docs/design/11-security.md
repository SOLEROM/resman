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
| `POST /api/sessions` `brief` | ≤ 16000 characters; `\r\n` normalised, a leading BOM dropped; no control, format or line-separator character besides tab and newline (Unicode Cc, Cf, Zl, Zp, Cs — C1 controls, bidi overrides, zero-width characters, surrogates; the 400 names the code point); no line equal to a brief marker; only with `bootstrap_new_vault`; pasted only into the operator's own Claude session, and the skill is told it is content, never instructions (`new_vault.clean_brief`). The form applies the same rules before it scaffolds anything |
| `POST /api/sessions` `interview` | `short` or `full`; only with `bootstrap_new_vault` (`new_vault.check_interview`) |
| `POST /api/sessions` `deep_list`, `autoresearch` | JSON booleans only (a string never switches a stage on); `autoresearch` needs `deep_list`; only with the deep message (`new_vault.check_stages`). They add fixed text plus a skill line rendered from the stored settings; nothing from the request is pasted |
| `POST /api/sessions` `autoresearch_top` | absent or `null` means every open value of the deep list; otherwise a whole number from 1 to 50 (`bool` and floats rejected), checked whenever given; the operator's own cap on how many autoresearch runs one session starts |
| YAML config content | `yaml.safe_load()` only; result must be a dict; validated before write |
| File size (config saves) | Reject content exceeding 1 MB |

A brief file for the New Vault form is read **in the browser** (`<input
type="file">` + `FileReader`) and sent as text: no endpoint reads a host file
into a Claude session. `GET /api/fs/list` (directories only, for the vault
path picker) stays the only filesystem-walking endpoint; its localhost-only
justification is weaker on a `--public` unit, which is why the brief did not
get a file-read sibling (docs/vaultBrief-plan.md, D3).

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
