"""Fetch Claude usage limits from claude.ai — ported from cld20's ``usage.sh``.

cld20 samples ``GET https://claude.ai/api/oauth/usage`` using the operator's
local OAuth token (``~/.claude/.credentials.json`` →
``claudeAiOauth.accessToken``) and reads two utilization percentages:

* ``five_hour.utilization`` — the rolling 5-hour **session** limit
* ``seven_day.utilization`` — the rolling 7-day **weekly** limit

These are the "10% session · 26% weekly" figures in the reference UI. The call
is read-only and spends **no** tokens (unlike cld20's optional ``claude -p``
wakeup, which we do not do). It is classified, never raised:

* ``ok``          — utilization numbers present
* ``auth_error``  — genuine 401/403, or missing/blank creds (logged out / token rejected)
* ``fetch_error`` — network failure, Cloudflare bot-challenge, or any other non-200

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


def fetch_usage(path: Optional[Path] = None, timeout: int = DEFAULT_TIMEOUT) -> dict:
    """Read creds, GET the usage endpoint, return a classified dict. Never raises.

    Tries ``bun`` first (the only client claude.ai's Cloudflare edge reliably
    lets through), then falls back to ``urllib``.
    """
    p = Path(path) if path else creds_path()
    try:
        creds = json.loads(p.read_text(encoding="utf-8"))
        token = creds["claudeAiOauth"]["accessToken"]
    except (OSError, ValueError, KeyError, TypeError):
        return _result("auth_error")
    if not token:
        return _result("auth_error")

    result = _fetch_via_bun(token, timeout)
    if result is None:
        result = _fetch_via_urllib(token, timeout)
    if result is None:
        return _result("fetch_error")
    status, body = result
    return parse_usage(status, body)
