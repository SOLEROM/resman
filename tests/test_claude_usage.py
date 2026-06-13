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
