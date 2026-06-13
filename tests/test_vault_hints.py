"""Tests for modules.vault_hints.read_hint — the landing-page hint reader."""
import json
from pathlib import Path

from modules import vault_hints


def write_hint(vault: Path, payload) -> None:
    wiki = vault / "wiki"
    wiki.mkdir(parents=True, exist_ok=True)
    body = payload if isinstance(payload, str) else json.dumps(payload)
    (wiki / "hint.json").write_text(body, encoding="utf-8")


def test_missing_file_returns_none(tmp_path):
    assert vault_hints.read_hint(tmp_path / "nope") is None
    # Vault exists but has no wiki/hint.json.
    (tmp_path / "empty").mkdir()
    assert vault_hints.read_hint(tmp_path / "empty") is None


def test_valid_hint_is_cleaned(tmp_path):
    write_hint(tmp_path, {
        "label": "HW-RE",
        "summary": "Reverse engineering of embedded RF systems.",
        "tags": ["rf", "sdr", "v2x"],
        "updatedBy": "vladi_solov",
        "updatedAt": "2026-06-10T12:55:26.718Z",
        "source": "operator",
    })
    hint = vault_hints.read_hint(tmp_path)
    assert hint == {
        "label": "HW-RE",
        "summary": "Reverse engineering of embedded RF systems.",
        "tags": ["rf", "sdr", "v2x"],
        "updatedBy": "vladi_solov",
        "updatedAt": "2026-06-10T12:55:26.718Z",
        "source": "operator",
    }


def test_invalid_json_returns_none(tmp_path):
    write_hint(tmp_path, "{not valid json,,,")
    assert vault_hints.read_hint(tmp_path) is None


def test_non_object_json_returns_none(tmp_path):
    write_hint(tmp_path, ["a", "list", "is", "not", "an", "object"])
    assert vault_hints.read_hint(tmp_path) is None


def test_oversized_file_returns_none(tmp_path):
    huge = {"label": "x", "summary": "y" * (vault_hints.MAX_HINT_BYTES + 100)}
    write_hint(tmp_path, huge)
    assert vault_hints.read_hint(tmp_path) is None


def test_missing_fields_become_none_and_tags_default_empty(tmp_path):
    write_hint(tmp_path, {"label": "OnlyLabel"})
    hint = vault_hints.read_hint(tmp_path)
    assert hint["label"] == "OnlyLabel"
    assert hint["summary"] is None
    assert hint["updatedBy"] is None
    assert hint["tags"] == []


def test_non_string_fields_and_dirty_tags_are_sanitized(tmp_path):
    write_hint(tmp_path, {
        "label": 12345,                       # wrong type -> None
        "summary": {"nested": "obj"},          # wrong type -> None
        "tags": ["ok", 7, "", "  ", None, "two"],  # keep only non-empty strings
    })
    hint = vault_hints.read_hint(tmp_path)
    assert hint["label"] is None
    assert hint["summary"] is None
    assert hint["tags"] == ["ok", "two"]


def test_tags_non_list_becomes_empty(tmp_path):
    write_hint(tmp_path, {"label": "L", "tags": "rf,sdr"})
    assert vault_hints.read_hint(tmp_path)["tags"] == []


def test_tags_are_capped(tmp_path):
    write_hint(tmp_path, {"tags": [f"t{i}" for i in range(vault_hints.MAX_TAGS + 10)]})
    assert len(vault_hints.read_hint(tmp_path)["tags"]) == vault_hints.MAX_TAGS
