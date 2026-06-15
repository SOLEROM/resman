"""WindowStats — a lightweight, durable store of Claude usage readings.

This is the in-process analogue of cld20's append-only sample history. cld20
splits session and weekly snapshots into monthly JSONL files under
``/var/lib/garage/cld20/samples/`` and renders them as a time-series dashboard.
We keep a single self-pruning JSONL (``config/window_samples.jsonl``) — enough
to drive the Windows tab's session/weekly charts and per-window breakdown —
without garage's install-bound monthly-file/sudo machinery.

Each reading is one row::

    {
      "ts": 1718450100.0,            # epoch seconds (sort key)
      "at": "2026-06-15T14:55:00",   # ISO local time (display)
      "source": "opener"|"auto"|"manual",
      "session_pct": 41 | null,      # five_hour.utilization
      "weekly_pct": 63 | null,       # seven_day.utilization
      "session_resets_at": "..."|null,
      "weekly_resets_at": "..."|null,
      "reason": "ok"|"limit_reached"|"auth_error"|"fetch_error"|...,
      "window_index": 3 | null,      # which configured window (1-based)
      "window_count": 5 | null,      # how many windows that day
      "duration_ms": 1234 | null,
    }

Writes are append-only and crash-tolerant: a corrupt line is skipped on read,
never fatal. The file is rotated (rewritten with the surviving rows) when it
grows past a soft cap or holds rows older than the retention horizon, so it can
never grow without bound on a long-running server.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from collections import deque
from pathlib import Path
from typing import Optional

from .event_bus import EventBus, get_bus

log = logging.getLogger(__name__)

# Retention: keep at most this many days of readings, and never more than this
# many rows in memory/on disk. At the default 5×/day collection these caps are
# never hit; they only bound a high-rate or very-long-lived install.
DEFAULT_RETENTION_DAYS = 90
DEFAULT_MAX_ROWS = 5000
SOURCES = ("opener", "auto", "manual")


def _iso_local(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(ts))


class WindowStats:
    def __init__(self, path: Path, bus: Optional[EventBus] = None, *,
                 retention_days: int = DEFAULT_RETENTION_DAYS,
                 max_rows: int = DEFAULT_MAX_ROWS) -> None:
        self.path = Path(path)
        self.bus = bus or get_bus()
        self.retention_days = retention_days
        self.max_rows = max_rows
        # In-RAM mirror for fast reads; the JSONL on disk is the durable copy.
        self._rows: deque = deque(maxlen=max_rows)
        self._load()

    # ----- persistence -----
    def _load(self) -> None:
        """Read the JSONL (tolerating corrupt lines), prune, and rewrite if any
        rows were dropped so the on-disk file stays bounded across restarts."""
        if not self.path.exists():
            return
        rows = []
        try:
            with self.path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except (ValueError, TypeError):
                        continue  # skip a torn/corrupt line, never fatal
                    if isinstance(row, dict) and "ts" in row:
                        rows.append(row)
        except OSError as exc:
            log.warning("window_samples.jsonl unreadable (%s); starting empty", exc)
            return
        rows.sort(key=lambda r: r.get("ts", 0))
        kept = self._prune(rows)
        self._rows = deque(kept, maxlen=self.max_rows)
        if len(kept) != len(rows):
            # Dropped expired/over-cap rows on load — compact the file once.
            self._rewrite()

    def _prune(self, rows: list[dict]) -> list[dict]:
        cutoff = time.time() - self.retention_days * 86400
        fresh = [r for r in rows if r.get("ts", 0) >= cutoff]
        if len(fresh) > self.max_rows:
            fresh = fresh[-self.max_rows:]
        return fresh

    def _append_file(self, entry: dict) -> None:
        if not self.path:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError as exc:
            log.warning("window stats append failed (%s)", exc)

    def _rewrite(self) -> None:
        """Atomically rewrite the file from the in-RAM rows (compaction)."""
        if not self.path:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=".window_samples.", suffix=".tmp",
                                       dir=str(self.path.parent))
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                for row in self._rows:
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
        except OSError as exc:
            log.warning("window stats rewrite failed (%s)", exc)

    # ----- recording -----
    def record(self, *, source: str, session_pct=None, weekly_pct=None,
               session_resets_at: Optional[str] = None,
               weekly_resets_at: Optional[str] = None,
               reason: Optional[str] = None, window_index: Optional[int] = None,
               window_count: Optional[int] = None,
               duration_ms: Optional[int] = None,
               ts: Optional[float] = None) -> dict:
        """Append one usage reading; returns the stored row and emits
        ``window_sample_added`` so the open Windows tab can live-append it."""
        ts = ts if ts is not None else time.time()
        entry = {
            "ts": ts,
            "at": _iso_local(ts),
            "source": source if source in SOURCES else "auto",
            "session_pct": session_pct,
            "weekly_pct": weekly_pct,
            "session_resets_at": session_resets_at,
            "weekly_resets_at": weekly_resets_at,
            "reason": reason,
            "window_index": window_index,
            "window_count": window_count,
            "duration_ms": duration_ms,
        }
        self._rows.append(entry)
        self._append_file(entry)
        # Compact opportunistically once the file outgrows the row cap (the deque
        # already dropped the oldest from RAM; rewrite drops it from disk too).
        if len(self._rows) >= self.max_rows:
            self._rewrite()
        self.bus.emit("window_sample_added", entry)
        return entry

    # ----- reading -----
    def list(self, *, limit: int = 500, since_ts: Optional[float] = None,
             source: Optional[str] = None) -> list[dict]:
        items = list(self._rows)
        if since_ts is not None:
            items = [e for e in items if e.get("ts", 0) >= since_ts]
        if source in SOURCES:
            items = [e for e in items if e.get("source") == source]
        if limit and limit > 0:
            items = items[-limit:]
        return items

    def latest(self, *, with_reading: bool = True) -> Optional[dict]:
        """Most recent row. With ``with_reading`` (default) skip opener/empty rows
        that carry no utilization number — matching cld20's "canonical latest
        reading excludes openers"."""
        for row in reversed(self._rows):
            if not with_reading:
                return row
            if row.get("session_pct") is not None or row.get("weekly_pct") is not None:
                return row
        return None

    def summary(self) -> dict:
        rows = list(self._rows)
        latest = self.latest()
        return {
            "count": len(rows),
            "first_at": rows[0]["at"] if rows else None,
            "last_at": rows[-1]["at"] if rows else None,
            "latest": latest,
            "retention_days": self.retention_days,
        }

    def clear(self) -> None:
        self._rows.clear()
        if self.path:
            try:
                self.path.write_text("", encoding="utf-8")
            except OSError:
                pass
