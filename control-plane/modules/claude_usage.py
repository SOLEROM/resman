"""Fetch Claude usage limits from claude.ai — ported from cld20's ``usage.sh``.

cld20 samples ``GET https://claude.ai/api/oauth/usage`` using the operator's
local OAuth token (``~/.claude/.credentials.json`` →
``claudeAiOauth.accessToken``) and reads two utilization percentages:

* ``five_hour.utilization`` — the rolling 5-hour **session** limit
* ``seven_day.utilization`` — the rolling 7-day **weekly** limit

These are the "10% session · 26% weekly" figures in the reference UI. The
happy-path call is read-only and spends **no** tokens. It is classified, never
raised:

* ``ok``            — utilization numbers present
* ``limit_reached`` — account is at its usage limit (session synthesised to 100%)
* ``auth_error``    — genuine 401/403, or missing/blank creds (logged out / token rejected)
* ``fetch_error``   — network failure, Cloudflare bot-challenge, or any other non-200

Stale-token recovery
--------------------
The read uses the OAuth access token stored in ``.credentials.json`` as-is. That
token expires every few hours; nothing in resman refreshes it, so on a machine
where the ``claude`` CLI isn't run regularly the stored token goes stale and
claude.ai answers the read with a **401** — which we would otherwise report as
"logged out / token rejected (use Claude, then retry)" even though the *refresh*
token is perfectly valid.

So, exactly like cld20's ``usage.sh``, we automate that "use Claude, then retry":
**only** when the read comes back ``auth_error`` do we run ``claude -p "hi"`` once.
That wakeup (a) makes the CLI mint a fresh access token from the refresh token and
write it back to ``.credentials.json``, after which we re-read usage with the
fresh token; and (b) doubles as an at-limit canary — when the account is over its
limit the CLI says so, and we synthesise a ``limit_reached`` / 100%-session
reading (cld20's behaviour) if the endpoint itself still gives no number. The
wakeup spends at most one trivial message, never fires on the healthy 200 path,
and can be disabled with ``RESMAN_USAGE_WAKEUP=0`` (alias ``CLD20_WAKEUP=0``).

Transport
---------
claude.ai sits behind **Cloudflare bot management**, which fingerprints the
client's TLS handshake (JA3/JA4) and HTTP/2 settings — it does NOT key off the
``User-Agent`` or the ``Authorization`` header. Python's ``urllib`` (OpenSSL
fingerprint, HTTP/1.1) trips the challenge and gets a **403 "Just a moment…"**
interstitial *before the request ever reaches the API*, regardless of headers.
``bun``'s ``fetch`` (BoringSSL, browser-grade fingerprint + HTTP/2) passes.

So, exactly like cld20's ``usage.sh``, we shell out to ``bun -e`` for the GET
and parse the JSON in Python. ``urllib`` is kept only as a best-effort fallback
for hosts without ``bun`` — where it will usually get the Cloudflare block,
which we now classify as ``fetch_error`` (not ``auth_error``) so the UI doesn't
tell the operator to re-login when their token is perfectly valid.

Only the heavy cron → JSONL → charts sampling pipeline from cld20 stays out of
scope; this lightweight on-demand read is what the footer meters need.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

USAGE_URL = "https://claude.ai/api/oauth/usage"
CLIENT_NAME = "claude-code"
CLIENT_VERSION = "2.1.161"
USER_AGENT = f"claude-cli/{CLIENT_VERSION} (external, cli)"
DEFAULT_TIMEOUT = 12

# ── Stale-token / at-limit wakeup (cld20's `claude -p "hi"` canary) ──────────
# Runs only on the auth_error fallback. The prompt is intentionally trivial so a
# healthy account spends near-nothing; an at-limit account spends nothing (the
# CLI is rejected before inference).
WAKEUP_PROMPT = "hi"
# Seconds to allow the wakeup. It runs inside the interactive /api/window/sync
# request, so keep it bounded; a `claude -p "hi"` round-trip (+ token refresh) is
# normally a few seconds. A timeout is classified as "fail" → keeps auth_error.
WAKEUP_TIMEOUT = 30

# Classify a non-zero `claude -p` exit by its output (mirrors cld20/usage.sh).
_LIMIT_RE = re.compile(
    r"usage\s*limit|rate\s*limit|reached\s*your|quota|too\s*many\s*requests|\b429\b", re.I)
_AUTH_RE = re.compile(
    r"log\s*in|authenticat|credential|invalid\s*api\s*key|unauthor|/login|oauth\s*token|not\s*logged",
    re.I)


def creds_path() -> Path:
    """Path to the OAuth credentials file.

    ``CLD20_CREDS_PATH`` overrides (same env var cld20 honours); the default is
    ``~/.claude/.credentials.json``.
    """
    override = os.environ.get("CLD20_CREDS_PATH")
    return Path(override) if override else Path.home() / ".claude" / ".credentials.json"


def _result(reason: str, http_status: Optional[int] = None) -> dict:
    return {
        "ok": reason == "ok",
        "reason": reason,
        "http_status": http_status,
        "session_pct": None,
        "session_resets_at": None,
        "weekly_pct": None,
        "weekly_resets_at": None,
    }


def _num(value):
    """Return a numeric utilization or None — never coerce a missing field to 0."""
    if isinstance(value, bool):
        return None
    return value if isinstance(value, (int, float)) else None


def _is_cloudflare_challenge(body: str) -> bool:
    """True if ``body`` is Cloudflare's bot-challenge interstitial, not an API
    response. Such a page rides on a 403 but means "the request never reached
    the API" — so it must not be read as an auth failure."""
    if not body:
        return False
    head = body.lstrip()[:1000].lower()
    if "<!doctype html" not in head and "<html" not in head:
        return False
    return (
        "just a moment" in head
        or "cloudflare" in head
        or "cf-ray" in head
        or "cf-chl" in head
        or "challenge-platform" in head
    )


def parse_usage(http_status: int, body: str) -> dict:
    """Pure parser/classifier — no network, so it is unit-testable in isolation."""
    # A Cloudflare bot-challenge (HTML interstitial, usually 403) is not an auth
    # failure: the token is fine, the request just never reached the API. Treat
    # it as a transient fetch_error so the UI doesn't say "logged out".
    if _is_cloudflare_challenge(body):
        return _result("fetch_error", http_status)
    if http_status in (401, 403):
        return _result("auth_error", http_status)
    if http_status != 200:
        return _result("fetch_error", http_status)
    try:
        data = json.loads(body)
    except (ValueError, TypeError):
        return _result("fetch_error", http_status)
    if not isinstance(data, dict):
        return _result("fetch_error", http_status)
    fh = data.get("five_hour") or {}
    sd = data.get("seven_day") or {}
    out = _result("ok", http_status)
    out["session_pct"] = _num(fh.get("utilization"))
    out["session_resets_at"] = fh.get("resets_at") or None
    out["weekly_pct"] = _num(sd.get("utilization"))
    out["weekly_resets_at"] = sd.get("resets_at") or None
    return out


# Bun snippet: GET the usage endpoint and print "<status>\n<body>". Mirrors
# cld20/usage.sh — it never throws; a caught error comes back as status -1.
_BUN_SNIPPET = (
    'let status=0,body="";'
    'try{'
    'const r=await fetch("' + USAGE_URL + '",{headers:{'
    'Authorization:"Bearer "+process.env.CLD20_TOKEN,'
    '"Content-Type":"application/json",'
    '"anthropic-client-name":"' + CLIENT_NAME + '",'
    '"anthropic-client-version":"' + CLIENT_VERSION + '"}});'
    "status=r.status;body=await r.text();"
    "}catch(e){status=-1;body=String((e&&e.message)||e);}"
    "console.log(status);console.log(body);"
)


def find_bun() -> Optional[str]:
    """Locate the ``bun`` binary robustly — the server process often runs with a
    bare PATH that lacks ``~/.bun/bin`` (where bun installs by default), so
    ``shutil.which`` alone returns None even though the operator has bun. Honour
    an explicit ``CLD20_BUN`` / ``RESMAN_BUN`` override, then PATH, then the
    common install locations."""
    override = os.environ.get("CLD20_BUN") or os.environ.get("RESMAN_BUN")
    if override and os.access(override, os.X_OK):
        return override
    found = shutil.which("bun")
    if found:
        return found
    candidates = [
        Path.home() / ".bun" / "bin" / "bun",
        Path("/usr/local/bin/bun"),
        Path("/opt/homebrew/bin/bun"),
        Path("/usr/bin/bun"),
    ]
    for c in candidates:
        if os.access(c, os.X_OK):
            return str(c)
    return None


def _fetch_via_bun(token: str, timeout: int):
    """GET the usage endpoint via ``bun -e`` (browser-grade TLS, so Cloudflare's
    bot challenge passes). Returns ``(status, body)``, or ``None`` if bun is
    unavailable, failed to launch, or its own ``fetch`` threw (network) — in
    which case the caller falls back to urllib."""
    bun = find_bun()
    if not bun:
        log.info("bun not found (PATH or ~/.bun/bin); falling back to urllib")
        return None
    try:
        proc = subprocess.run(
            [bun, "-e", _BUN_SNIPPET],
            capture_output=True,
            text=True,
            timeout=timeout + 3,
            env={**os.environ, "CLD20_TOKEN": token},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.info("bun usage fetch failed to run: %s", exc)
        return None
    if proc.returncode != 0:
        log.info("bun usage fetch exited %s: %s", proc.returncode, proc.stderr.strip())
        return None
    status_line, _, body = proc.stdout.partition("\n")
    try:
        status = int(status_line.strip())
    except ValueError:
        return None
    if status < 0:
        # bun's fetch threw (network/DNS/TLS) — let the caller try urllib.
        log.info("bun usage fetch network error: %s", body.strip()[:200])
        return None
    return status, body


def _fetch_via_urllib(token: str, timeout: int):
    """Best-effort fallback for hosts without bun. claude.ai's Cloudflare edge
    usually serves a 403 bot-challenge here (classified as fetch_error upstream)
    — see the module docstring. Returns ``(status, body)`` or ``None``."""
    req = urllib.request.Request(USAGE_URL, headers={
        "Authorization": "Bearer " + token,
        "Content-Type": "application/json",
        "anthropic-client-name": CLIENT_NAME,
        "anthropic-client-version": CLIENT_VERSION,
        "User-Agent": USER_AGENT,
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode("utf-8", "replace")
        except Exception:
            body = ""
        return exc.code, body
    except Exception as exc:  # network/SSL/timeout — classified, not fatal
        log.info("urllib usage fetch failed: %s", exc)
        return None


def _read_token(path: Path) -> Optional[str]:
    """Pull ``claudeAiOauth.accessToken`` from the creds file, or None if the file
    is missing/unreadable/malformed or the token is blank."""
    try:
        creds = json.loads(path.read_text(encoding="utf-8"))
        token = creds["claudeAiOauth"]["accessToken"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return token or None


def _fetch_with_token(token: str, timeout: int) -> dict:
    """GET the usage endpoint with a specific token and classify the response.

    Tries ``bun`` first (the only client claude.ai's Cloudflare edge reliably
    lets through), then falls back to ``urllib``.
    """
    result = _fetch_via_bun(token, timeout)
    if result is None:
        result = _fetch_via_urllib(token, timeout)
    if result is None:
        return _result("fetch_error")
    status, body = result
    return parse_usage(status, body)


def classify_wakeup(returncode: int, output: str) -> str:
    """Map a ``claude -p`` result to a wakeup state — pure, so it's unit-testable
    without spawning the CLI. Mirrors cld20/usage.sh's precedence:
    ``ok`` (exit 0) | ``limit`` | ``auth`` | ``fail``."""
    if returncode == 0:
        return "ok"
    text = output or ""
    if _LIMIT_RE.search(text):
        return "limit"
    if _AUTH_RE.search(text):
        return "auth"
    return "fail"


def find_claude() -> Optional[str]:
    """Locate the ``claude`` CLI robustly (same rationale as :func:`find_bun` —
    the server's PATH often lacks ``~/.local/bin``). Honours a ``CLD20_CLAUDE`` /
    ``RESMAN_CLAUDE`` override, then PATH, then the common install locations."""
    override = os.environ.get("CLD20_CLAUDE") or os.environ.get("RESMAN_CLAUDE")
    if override and os.access(override, os.X_OK):
        return override
    found = shutil.which("claude")
    if found:
        return found
    candidates = [
        Path.home() / ".local" / "bin" / "claude",
        Path.home() / ".claude" / "local" / "claude",
        Path("/usr/local/bin/claude"),
        Path("/usr/bin/claude"),
    ]
    for c in candidates:
        if os.access(c, os.X_OK):
            return str(c)
    return None


def _wakeup_enabled() -> bool:
    """The stale-token wakeup is on by default; ``RESMAN_USAGE_WAKEUP=0`` (alias
    ``CLD20_WAKEUP=0``) turns it off for hosts that must never spend a token."""
    v = os.environ.get("RESMAN_USAGE_WAKEUP")
    if v is None:
        v = os.environ.get("CLD20_WAKEUP")
    if v is None:
        return True
    return v.strip().lower() not in ("0", "false", "no", "off")


def _run_claude_canary(timeout: int) -> Optional[str]:
    """Run ``claude -p "hi"`` once and return its wakeup state, or ``None`` if the
    CLI is unavailable / failed to launch (so the caller keeps the prior result).

    Side effect: the CLI refreshes the OAuth access token on disk when it's stale
    — which is the whole point of running it on the auth_error path.
    """
    claude = find_claude()
    if not claude:
        log.info("claude CLI not found (PATH or ~/.local/bin); cannot refresh stale token")
        return None
    try:
        proc = subprocess.run(
            [claude, "-p", WAKEUP_PROMPT, "--output-format", "text"],
            capture_output=True,
            text=True,
            timeout=max(timeout, WAKEUP_TIMEOUT),
            stdin=subprocess.DEVNULL,
            env={**os.environ},
        )
    except subprocess.TimeoutExpired:
        log.info("claude wakeup timed out after %ss", max(timeout, WAKEUP_TIMEOUT))
        return "fail"
    except (OSError, subprocess.SubprocessError) as exc:
        log.info("claude wakeup failed to run: %s", exc)
        return None
    state = classify_wakeup(proc.returncode, f"{proc.stdout or ''}\n{proc.stderr or ''}")
    log.info("claude wakeup state=%s (rc=%s)", state, proc.returncode)
    return state


def _synthesised_limit(prev: dict) -> dict:
    """An at-limit reading when the usage endpoint gave no session number: record
    a plain 100% session (cld20's behaviour), preserving any weekly number the
    retry managed to read."""
    out = _result("limit_reached", prev.get("http_status"))
    out["ok"] = True
    out["session_pct"] = 100
    out["weekly_pct"] = prev.get("weekly_pct")
    out["weekly_resets_at"] = prev.get("weekly_resets_at")
    return out


def _recover_from_auth_error(path: Path, prior: dict, timeout: int) -> Optional[dict]:
    """The read came back ``auth_error`` (stored token rejected). Run the cld20
    wakeup to refresh the token / detect an at-limit account, then retry. Returns
    a fresh result, or ``None`` to keep ``prior`` (wakeup disabled/unavailable, or
    the account is genuinely logged out)."""
    if not _wakeup_enabled():
        return None
    state = _run_claude_canary(timeout)
    if state is None or state in ("auth", "fail"):
        # No CLI, or the CLI itself is logged out / broken → genuinely auth_error.
        return None
    # state in ("ok", "limit"): the wakeup just refreshed the token on disk.
    token = _read_token(path)
    retry = _fetch_with_token(token, timeout) if token else _result("auth_error")
    if retry["reason"] == "ok":
        if state == "limit":
            # Real numbers AND the CLI says at-limit — flag it for the UI.
            retry["reason"] = "limit_reached"
        return retry
    if state == "limit":
        return _synthesised_limit(retry)
    # state == "ok" but the retry still failed: surface the retry's reason
    # (fetch_error/auth_error) rather than masking it.
    return retry


def fetch_usage(path: Optional[Path] = None, timeout: int = DEFAULT_TIMEOUT) -> dict:
    """Read creds, GET the usage endpoint, return a classified dict. Never raises.

    Healthy path: a single read-only GET (no token spent). If that GET is
    rejected as ``auth_error`` (a stale stored token, the common "logged out /
    token rejected" false alarm), fall back to cld20's ``claude -p "hi"`` wakeup
    to refresh the token and detect an at-limit account, then retry — see the
    module docstring.
    """
    p = Path(path) if path else creds_path()
    token = _read_token(p)
    if not token:
        # No token at all → truly logged out; a wakeup can't refresh nothing.
        return _result("auth_error")

    out = _fetch_with_token(token, timeout)
    if out["reason"] != "auth_error":
        return out
    recovered = _recover_from_auth_error(p, out, timeout)
    return recovered if recovered is not None else out
