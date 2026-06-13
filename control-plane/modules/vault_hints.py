"""Read per-vault ``wiki/hint.json`` files for the landing page.

Each vault's Claude wiki plugin writes ``wiki/hint.json`` — a small,
generated description of what the vault is about. The landing page renders
one thumbnail card per vault from this data.

Reading is strictly best-effort: a missing, oversized, or malformed
hint.json yields ``None`` so the card falls back to the bare vault name.
Only a whitelisted set of string/array fields is surfaced, so a
hand-edited hint.json can never inject arbitrary structure into the API
response.

Shape produced by ``read_hint`` (every field nullable except ``tags``):
    {label, summary, tags: [...], updatedBy, updatedAt, source}
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

# Path of the hint file relative to the vault root.
HINT_REL = "wiki/hint.json"
# Generous ceiling — a real hint.json is well under 4 KB. Anything larger is
# almost certainly not the file we expect, so we skip it rather than parse it.
MAX_HINT_BYTES = 64 * 1024
# Defensive cap so a runaway tag list can't bloat the landing payload.
MAX_TAGS = 24
# Only these keys are read from the file; everything else is ignored.
_STRING_FIELDS = ("label", "summary", "updatedBy", "updatedAt", "source")


def read_hint(vault_path: str | Path) -> Optional[dict]:
    """Return the cleaned hint dict for a vault, or ``None``.

    ``None`` is returned when the file is absent, too large, unreadable,
    not valid JSON, or not a JSON object. This function never raises.
    """
    try:
        hint_file = Path(vault_path) / HINT_REL
        if not hint_file.is_file():
            return None
        if hint_file.stat().st_size > MAX_HINT_BYTES:
            return None
        raw = hint_file.read_text(encoding="utf-8", errors="replace")
        data = json.loads(raw)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return _clean(data)


def _clean(data: dict) -> dict:
    """Project the raw JSON onto the whitelisted, type-checked shape."""
    out: dict = {}
    for key in _STRING_FIELDS:
        value = data.get(key)
        out[key] = value if isinstance(value, str) else None
    raw_tags = data.get("tags")
    if isinstance(raw_tags, list):
        tags = [t for t in raw_tags if isinstance(t, str) and t.strip()][:MAX_TAGS]
    else:
        tags = []
    out["tags"] = tags
    return out
