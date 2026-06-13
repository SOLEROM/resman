"""Tests for the Claude usage fetcher (modules/claude_usage.py).

parse_usage is a pure classifier (no network); fetch_usage is only exercised on
its no-network failure paths (missing/invalid creds) so the suite never calls
claude.ai.
"""
import json

from modules import claude_usage


def _body(five=None, seven=None):
    out = {}
    if five is not None:
        out["five_hour"] = five
    if seven is not None:
        out["seven_day"] = seven
    return json.dumps(out)


def test_parse_ok_extracts_session_and_weekly():
    body = _body(
        five={"utilization": 7.0, "resets_at": "2026-06-12T21:00:00Z"},
        seven={"utilization": 9, "resets_at": "2026-06-17T20:00:00Z"},
    )
    r = claude_usage.parse_usage(200, body)
    assert r["ok"] is True and r["reason"] == "ok"
    assert r["session_pct"] == 7.0
    assert r["weekly_pct"] == 9
    assert r["session_resets_at"] == "2026-06-12T21:00:00Z"
    assert r["weekly_resets_at"] == "2026-06-17T20:00:00Z"


def test_parse_auth_error_on_401_403():
    for status in (401, 403):
        r = claude_usage.parse_usage(status, "")
        assert r["reason"] == "auth_error"
        assert r["ok"] is False
        assert r["session_pct"] is None and r["weekly_pct"] is None


def test_parse_cloudflare_challenge_is_fetch_error_not_auth():
    # claude.ai's Cloudflare edge serves a 403 HTML interstitial to clients it
    # fingerprints as bots. The token is valid — the request never reached the
    # API — so this must be fetch_error, NOT auth_error ("logged out").
    body = (
        '<!DOCTYPE html><html lang="en-US"><head><title>Just a moment...</title>'
        '<meta name="robots" content="noindex,nofollow"></head><body></body></html>'
    )
    r = claude_usage.parse_usage(403, body)
    assert r["reason"] == "fetch_error"
    assert r["ok"] is False


def test_parse_fetch_error_on_500():
    r = claude_usage.parse_usage(500, "")
    assert r["reason"] == "fetch_error"


def test_parse_fetch_error_on_bad_json():
    r = claude_usage.parse_usage(200, "not json {{{")
    assert r["reason"] == "fetch_error"


def test_parse_missing_utilization_stays_none_not_zero():
    # A missing/blank utilization must NOT be coerced to 0 (silent bad reading).
    r = claude_usage.parse_usage(200, _body(five={"resets_at": "x"}, seven={}))
    assert r["reason"] == "ok"
    assert r["session_pct"] is None
    assert r["weekly_pct"] is None


def test_fetch_missing_creds_is_auth_error(tmp_path):
    # Nonexistent creds file → auth_error, no network call.
    r = claude_usage.fetch_usage(path=tmp_path / "nope.json")
    assert r["reason"] == "auth_error"
    assert r["ok"] is False


def test_fetch_creds_without_token_is_auth_error(tmp_path):
    p = tmp_path / "creds.json"
    p.write_text(json.dumps({"claudeAiOauth": {"refreshToken": "x"}}))
    r = claude_usage.fetch_usage(path=p)
    assert r["reason"] == "auth_error"


# ── Wakeup classifier (pure; no CLI spawned) ────────────────────────────────
def test_classify_wakeup_states():
    assert claude_usage.classify_wakeup(0, "whatever") == "ok"
    assert claude_usage.classify_wakeup(1, "You've reached your usage limit") == "limit"
    assert claude_usage.classify_wakeup(1, "HTTP 429 too many requests") == "limit"
    assert claude_usage.classify_wakeup(1, "Please run /login to authenticate") == "auth"
    assert claude_usage.classify_wakeup(1, "invalid api key") == "auth"
    assert claude_usage.classify_wakeup(1, "some unexpected crash") == "fail"


# ── Stale-token recovery: auth_error → claude wakeup → retry ─────────────────
def _creds(tmp_path, token="tok"):
    p = tmp_path / "creds.json"
    p.write_text(json.dumps({"claudeAiOauth": {"accessToken": token}}))
    return p


def _ok(session=None, weekly=None, status=200):
    r = claude_usage._result("ok", status)
    r["session_pct"] = session
    r["weekly_pct"] = weekly
    return r


def test_healthy_200_never_runs_the_wakeup(tmp_path, monkeypatch):
    seen = {"canary": False}
    monkeypatch.setattr(claude_usage, "_fetch_with_token", lambda *a: _ok(session=4.0))
    monkeypatch.setattr(claude_usage, "_run_claude_canary",
                        lambda t: seen.__setitem__("canary", True) or "ok")
    r = claude_usage.fetch_usage(path=_creds(tmp_path))
    assert r["reason"] == "ok" and r["session_pct"] == 4.0
    assert seen["canary"] is False  # zero token spend on the happy path


def test_auth_error_then_wakeup_refreshes_and_retry_succeeds(tmp_path, monkeypatch):
    seq = [claude_usage._result("auth_error", 401), _ok(session=12.0, weekly=3.0)]
    monkeypatch.setattr(claude_usage, "_fetch_with_token", lambda *a: seq.pop(0))
    monkeypatch.setattr(claude_usage, "_run_claude_canary", lambda t: "ok")
    r = claude_usage.fetch_usage(path=_creds(tmp_path))
    assert r["reason"] == "ok"
    assert r["session_pct"] == 12.0 and r["weekly_pct"] == 3.0


def test_auth_error_at_limit_returns_real_numbers_flagged(tmp_path, monkeypatch):
    # Account at limit: the refreshed retry returns the real 100% — flag it.
    seq = [claude_usage._result("auth_error", 401), _ok(session=100, weekly=17)]
    monkeypatch.setattr(claude_usage, "_fetch_with_token", lambda *a: seq.pop(0))
    monkeypatch.setattr(claude_usage, "_run_claude_canary", lambda t: "limit")
    r = claude_usage.fetch_usage(path=_creds(tmp_path))
    assert r["reason"] == "limit_reached"
    assert r["session_pct"] == 100 and r["weekly_pct"] == 17


def test_auth_error_at_limit_synthesises_100_when_endpoint_silent(tmp_path, monkeypatch):
    # At limit AND the usage endpoint still won't answer → synthesise 100% session.
    monkeypatch.setattr(claude_usage, "_fetch_with_token",
                        lambda *a: claude_usage._result("auth_error", 401))
    monkeypatch.setattr(claude_usage, "_run_claude_canary", lambda t: "limit")
    r = claude_usage.fetch_usage(path=_creds(tmp_path))
    assert r["reason"] == "limit_reached"
    assert r["session_pct"] == 100
    assert r["ok"] is True


def test_auth_error_genuine_logout_stays_auth_error(tmp_path, monkeypatch):
    monkeypatch.setattr(claude_usage, "_fetch_with_token",
                        lambda *a: claude_usage._result("auth_error", 401))
    monkeypatch.setattr(claude_usage, "_run_claude_canary", lambda t: "auth")
    r = claude_usage.fetch_usage(path=_creds(tmp_path))
    assert r["reason"] == "auth_error"


def test_auth_error_wakeup_unavailable_stays_auth_error(tmp_path, monkeypatch):
    monkeypatch.setattr(claude_usage, "_fetch_with_token",
                        lambda *a: claude_usage._result("auth_error", 401))
    monkeypatch.setattr(claude_usage, "_run_claude_canary", lambda t: None)  # no CLI
    r = claude_usage.fetch_usage(path=_creds(tmp_path))
    assert r["reason"] == "auth_error"


def test_wakeup_can_be_disabled_by_env(tmp_path, monkeypatch):
    monkeypatch.setenv("RESMAN_USAGE_WAKEUP", "0")
    seen = {"canary": False}
    monkeypatch.setattr(claude_usage, "_fetch_with_token",
                        lambda *a: claude_usage._result("auth_error", 401))
    monkeypatch.setattr(claude_usage, "_run_claude_canary",
                        lambda t: seen.__setitem__("canary", True) or "ok")
    r = claude_usage.fetch_usage(path=_creds(tmp_path))
    assert r["reason"] == "auth_error"
    assert seen["canary"] is False  # disabled → CLI never spawned


def test_fetch_error_does_not_trigger_wakeup(tmp_path, monkeypatch):
    # Network/Cloudflare failures aren't a stale token — don't spend a wakeup.
    seen = {"canary": False}
    monkeypatch.setattr(claude_usage, "_fetch_with_token",
                        lambda *a: claude_usage._result("fetch_error", 403))
    monkeypatch.setattr(claude_usage, "_run_claude_canary",
                        lambda t: seen.__setitem__("canary", True) or "ok")
    r = claude_usage.fetch_usage(path=_creds(tmp_path))
    assert r["reason"] == "fetch_error"
    assert seen["canary"] is False
