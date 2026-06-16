"""TaskManager — prioritized task queue backed by an append-only JSONL event log.

Tasks are commands to run against a vault (plugin operations or shell). Each
state change is an appended event in `config/tasks.jsonl`. Current state is
derived by replaying. An in-memory index is built at startup and maintained
incrementally. A dispatch mutex prevents concurrent dispatch races.

All subprocess calls use argument-list form. shell=True / sh -c are
prohibited. params.url is validated as HTTP/HTTPS. params.topic and
params.prompt are limited to 200 chars of printable ASCII.
"""
from __future__ import annotations

import json
import logging
import os
import pty
import re
import shlex
import string
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional
from urllib.parse import urlparse

from . import plugin_commands
from .event_bus import EventBus, get_bus

log = logging.getLogger(__name__)

PRIORITIES = ("high", "medium", "low")
STATES = (
    "pending",
    "running",
    "completed",
    "failed",
    "deferred",
    "scheduled",
    "cancelled",
    "interrupted",
    "archived",
)
OPERATIONS = (
    "wiki-ingest",
    "wiki-ingest-prefix",
    "wiki-lint",
    "wiki-autoresearch",
    "wiki-canvas",
    "wiki-update-hot-cache",
    "wiki-bootstrap",
    "wiki-hint",
    "run-prompt",
    "run-shell",
)

URL_INGEST_PREFIX_FILE = "prompts/urlInjestPrefix.md"

PRINTABLE_ASCII = set(string.printable) - set("\x0b\x0c")
PRINTABLE_RE = re.compile(r"^[\x20-\x7E\t\n\r]*$")
NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")

COMPACTION_THRESHOLD = 50000  # lines
LOG_MAX_BYTES = 5 * 1024 * 1024  # cap streaming log file per task at 5 MB


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_iso(ts: str) -> Optional[datetime]:
    """Parse an ISO 8601 timestamp; treat naive timestamps as UTC."""
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _pid_alive(pid: int) -> bool:
    """Return True if a process with the given PID is currently alive.

    Uses os.kill(pid, 0) which raises ProcessLookupError if the process is
    gone and PermissionError if the process exists but we can't signal it
    (still alive). Returns False on any other error.
    """
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def _validate_params(operation: str, params: dict) -> dict:
    params = dict(params or {})
    if operation == "wiki-ingest":
        url = params.get("url")
        if not isinstance(url, str) or not url:
            raise ValueError("wiki-ingest: 'url' required")
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            raise ValueError("wiki-ingest: 'url' must be http or https")
        params["update_canvas"] = bool(params.get("update_canvas"))
    elif operation == "wiki-ingest-prefix":
        url = params.get("url")
        if not isinstance(url, str) or not url:
            raise ValueError("wiki-ingest-prefix: 'url' required")
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            raise ValueError("wiki-ingest-prefix: 'url' must be http or https")
        params["update_canvas"] = bool(params.get("update_canvas"))
    elif operation == "wiki-autoresearch":
        topic = params.get("topic", "")
        if not isinstance(topic, str) or not topic:
            raise ValueError("wiki-autoresearch: 'topic' required")
        if len(topic) > 200 or not PRINTABLE_RE.match(topic):
            raise ValueError("wiki-autoresearch: topic must be ≤200 chars printable ASCII")
    elif operation == "wiki-canvas":
        description = params.get("description", "")
        if description is None:
            description = ""
        if not isinstance(description, str):
            raise ValueError("wiki-canvas: 'description' must be a string")
        if len(description) > 200 or not PRINTABLE_RE.match(description):
            raise ValueError("wiki-canvas: description must be ≤200 chars printable ASCII")
        params["description"] = description
    elif operation == "run-prompt":
        prompt = params.get("prompt", "")
        if not isinstance(prompt, str) or not prompt:
            raise ValueError("run-prompt: 'prompt' required")
        if len(prompt) > 200 or not PRINTABLE_RE.match(prompt):
            raise ValueError("run-prompt: prompt must be ≤200 chars printable ASCII")
    elif operation == "run-shell":
        cmd_parts = params.get("cmd_parts")
        if not isinstance(cmd_parts, list) or not cmd_parts:
            raise ValueError("run-shell: 'cmd_parts' must be a non-empty list")
        for p in cmd_parts:
            if not isinstance(p, str):
                raise ValueError("run-shell: cmd_parts must all be strings")
    return params


@dataclass
class Task:
    id: str
    name: str
    vault: str
    operation: str
    params: dict
    priority: str
    schedule: str = "background"
    parent_id: Optional[str] = None
    state: str = "pending"
    created_at: str = ""
    updated_at: str = ""
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    exit_code: Optional[int] = None
    error: Optional[str] = None
    pid: Optional[int] = None
    scheduled_for: Optional[str] = None
    # Opt-in usage-limit check (the trigger's "check limits" toggle): when set,
    # a claude.ai usage reading is captured just before the task starts and just
    # after it finishes. Both readings are stored verbatim (see claude_usage).
    check_limits: bool = False
    usage_before: Optional[dict] = None
    usage_after: Optional[dict] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "vault": self.vault,
            "operation": self.operation,
            "params": dict(self.params),
            "priority": self.priority,
            "schedule": self.schedule,
            "parent_id": self.parent_id,
            "state": self.state,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "exit_code": self.exit_code,
            "error": self.error,
            "pid": self.pid,
            "scheduled_for": self.scheduled_for,
            "check_limits": self.check_limits,
            "usage_before": dict(self.usage_before) if self.usage_before else None,
            "usage_after": dict(self.usage_after) if self.usage_after else None,
        }


class TaskManager:
    """Owns the JSONL event log and the in-memory state index.

    Subprocess execution is delegated to a runner callable so tests can swap it
    out. The default runner uses subprocess.run with argument lists.
    """

    def __init__(
        self,
        log_path: Path,
        log_dir: Path,
        resman_root: Path,
        is_window_active: Callable[[], bool],
        get_vault_path: Callable[[str], Optional[str]],
        list_vault_names: Callable[[], List[str]],
        bus: Optional[EventBus] = None,
        runner: Optional[Callable] = None,
        usage_provider: Optional[Callable[[], dict]] = None,
    ) -> None:
        self.log_path = Path(log_path)
        self.log_dir = Path(log_dir)
        self.resman_root = Path(resman_root)
        self.is_window_active = is_window_active
        self.get_vault_path = get_vault_path
        self.list_vault_names = list_vault_names
        self.bus = bus or get_bus()
        self._tasks: Dict[str, Task] = {}
        self._dispatch_lock = threading.RLock()
        self._write_lock = threading.RLock()
        self._line_count = 0
        self._bad_line_count = 0
        self._partial_truncated = False
        self._integrity_warnings: List[str] = []
        # If a runner is injected (tests), it has the legacy 3-arg signature:
        # runner(cmd, cwd, log_file) -> int. When None, the production
        # streaming runner is used; it writes the same log file and additionally
        # emits task_log_appended events on the bus for live tailing.
        self._runner = runner
        # Reads a classified claude.ai usage dict (claude_usage.fetch_usage).
        # Used only for tasks created with check_limits=True; None disables the
        # whole feature (e.g. in unit tests), so sampling becomes a no-op.
        self._usage_provider = usage_provider
        self._procs: Dict[str, subprocess.Popen] = {}
        self._executor: Optional[Callable[[Task], None]] = None
        self.bus.subscribe("window_activated", self._on_window_activated)

    # ----- Replay / persistence -----
    def replay(self) -> dict:
        """Replay tasks.jsonl into the in-memory index. Crash-consistent."""
        self._tasks = {}
        self._bad_line_count = 0
        self._partial_truncated = False
        self._line_count = 0
        if not self.log_path.exists():
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            self.log_path.touch()
            return self._replay_summary()
        # Crash-consistent: detect partial last line (no trailing newline)
        self._truncate_partial_last_line()
        with self.log_path.open("r", encoding="utf-8") as f:
            offset = 0
            for raw in f:
                self._line_count += 1
                offset += len(raw.encode("utf-8"))
                line = raw.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError as exc:
                    self._bad_line_count += 1
                    log.warning(
                        "tasks.jsonl: bad line near offset %d (%s) — skipped",
                        offset, exc,
                    )
                    continue
                self._apply(event)
        # Detect tasks left in 'started'/'running' with no terminal event.
        # If the recorded PID is still alive, the process survived a control-plane
        # restart and the task is genuinely still running — don't mark interrupted.
        for tid, task in self._tasks.items():
            if task.state == "running":
                if task.pid and _pid_alive(task.pid):
                    continue
                task.state = "interrupted"
                task.pid = None
                self._integrity_warnings.append(
                    f"task {tid} was running at last shutdown — marked interrupted"
                )
            elif task.state == "scheduled":
                sched = _parse_iso(task.scheduled_for or "")
                if sched is not None and sched <= datetime.now(timezone.utc):
                    self._integrity_warnings.append(
                        f"task {tid} scheduled for {task.scheduled_for} is overdue"
                    )
        return self._replay_summary()

    def _replay_summary(self) -> dict:
        return {
            "lines": self._line_count,
            "bad_lines": self._bad_line_count,
            "partial_truncated": self._partial_truncated,
            "tasks": len(self._tasks),
            "warnings": list(self._integrity_warnings),
        }

    def _truncate_partial_last_line(self) -> None:
        try:
            sz = self.log_path.stat().st_size
        except OSError:
            return
        if sz == 0:
            return
        with self.log_path.open("rb+") as f:
            f.seek(sz - 1)
            last = f.read(1)
            if last != b"\n":
                # Find the previous newline
                f.seek(0)
                data = f.read()
                idx = data.rfind(b"\n")
                if idx == -1:
                    f.truncate(0)
                else:
                    f.truncate(idx + 1)
                self._partial_truncated = True

    def _apply(self, event: dict) -> None:
        """Apply a single JSONL event to the in-memory state."""
        kind = event.get("event")
        tid = event.get("task_id")
        if kind == "created":
            d = event.get("data") or {}
            try:
                t = Task(
                    id=tid,
                    name=d.get("name", ""),
                    vault=d.get("vault", ""),
                    operation=d.get("operation", ""),
                    params=d.get("params", {}),
                    priority=d.get("priority", "medium"),
                    schedule=d.get("schedule", "background"),
                    parent_id=d.get("parent_id"),
                    state="pending",
                    created_at=event.get("ts", ""),
                    updated_at=event.get("ts", ""),
                    check_limits=bool(d.get("check_limits", False)),
                )
            except Exception:
                return
            self._tasks[tid] = t
            return
        task = self._tasks.get(tid)
        if not task:
            return
        ts = event.get("ts", "")
        task.updated_at = ts
        if kind == "started":
            task.state = "running"
            task.started_at = ts
            pid = event.get("pid")
            if isinstance(pid, int):
                task.pid = pid
        elif kind == "completed":
            task.state = "completed"
            task.finished_at = ts
            task.exit_code = event.get("exit_code", 0)
            task.pid = None
        elif kind == "failed":
            task.state = "failed"
            task.finished_at = ts
            task.exit_code = event.get("exit_code", 1)
            task.error = event.get("error")
            task.pid = None
        elif kind == "deferred":
            task.state = "deferred"
        elif kind == "scheduled":
            task.state = "scheduled"
            sf = event.get("scheduled_for")
            if isinstance(sf, str):
                task.scheduled_for = sf
        elif kind == "promoted":
            task.state = "pending"
        elif kind == "interrupted":
            task.state = "interrupted"
            task.finished_at = ts
            task.pid = None
        elif kind == "cancelled":
            task.state = "cancelled"
            task.finished_at = ts
            task.pid = None
        elif kind == "archived":
            task.state = "archived"
        elif kind == "updated":
            data = event.get("data") or {}
            for k in ("priority", "params", "name"):
                if k in data:
                    setattr(task, k, data[k])
        elif kind == "usage_sampled":
            # A "check limits" reading taken before/after the run. Stored verbatim
            # so the UI can show both points (and any delta) on the task.
            reading = event.get("data") or {}
            if event.get("phase") == "after":
                task.usage_after = reading
            else:
                task.usage_before = reading
        # child_created / dispatch_started / cron_skipped don't change task state directly

    def _append(self, event: dict) -> None:
        with self._write_lock:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(event, separators=(",", ":")) + "\n"
            with self.log_path.open("a", encoding="utf-8") as f:
                f.write(line)
                f.flush()
                try:
                    os.fsync(f.fileno())
                except OSError:
                    pass

    # ----- Public API -----
    def create_task(
        self,
        name: str,
        vault: str,
        operation: str,
        params: dict,
        priority: str = "medium",
        schedule: str = "background",
        parent_id: Optional[str] = None,
        run_now: bool = True,
        scheduled_for: Optional[str] = None,
        force: bool = False,
        check_limits: bool = False,
    ) -> Task:
        if not NAME_RE.match(name or ""):
            raise ValueError("task name must match [a-zA-Z0-9_-]")
        if priority not in PRIORITIES:
            raise ValueError(f"priority must be one of {PRIORITIES}")
        if operation not in OPERATIONS:
            raise ValueError(f"operation must be one of {OPERATIONS}")
        # vault: ALL allowed only if not a child
        if vault != "ALL":
            if not self.get_vault_path(vault):
                raise ValueError(f"vault {vault!r} is not registered")
        validated_params = _validate_params(operation, params)

        normalized_scheduled = None
        if scheduled_for:
            if vault == "ALL":
                raise ValueError(
                    "scheduled_for is not supported together with vault=ALL"
                )
            sched_dt = _parse_iso(scheduled_for)
            if sched_dt is None:
                raise ValueError("scheduled_for must be an ISO 8601 timestamp")
            if sched_dt <= datetime.now(timezone.utc):
                raise ValueError("scheduled_for must be in the future")
            # Normalize to a canonical UTC Z form so downstream consumers get
            # a single representation regardless of what the caller sent.
            normalized_scheduled = (
                sched_dt.astimezone(timezone.utc)
                .replace(microsecond=0)
                .isoformat()
                .replace("+00:00", "Z")
            )

        with self._dispatch_lock:
            if vault == "ALL" and parent_id is None:
                return self._create_parent_all(
                    name, operation, validated_params, priority, schedule, run_now,
                    force, check_limits,
                )
            return self._create_single(
                name, vault, operation, validated_params, priority, schedule,
                parent_id, run_now, normalized_scheduled, force, check_limits,
            )

    def _create_single(
        self,
        name: str,
        vault: str,
        operation: str,
        params: dict,
        priority: str,
        schedule: str,
        parent_id: Optional[str],
        run_now: bool,
        scheduled_for: Optional[str] = None,
        force: bool = False,
        check_limits: bool = False,
    ) -> Task:
        tid = f"t-{uuid.uuid4().hex[:12]}"
        ts = _utcnow_iso()
        data = {
            "name": name,
            "vault": vault,
            "operation": operation,
            "params": params,
            "priority": priority,
            "schedule": schedule,
            "parent_id": parent_id,
        }
        if check_limits:
            data["check_limits"] = True
        if scheduled_for:
            data["scheduled_for"] = scheduled_for
        self._append({"ts": ts, "event": "created", "task_id": tid, "data": data})
        if parent_id:
            self._append({"ts": ts, "event": "child_created", "task_id": parent_id, "child_id": tid})

        # Routing precedence: an explicit scheduled_for wins over window-gating.
        # A scheduled task waits for the Scheduler's one-shot trigger; it does
        # not auto-promote on window activation.
        if scheduled_for:
            self._append({
                "ts": ts, "event": "scheduled", "task_id": tid,
                "scheduled_for": scheduled_for,
            })
            initial_state = "scheduled"
        elif not force and not self.is_window_active():
            self._append({"ts": ts, "event": "deferred", "task_id": tid})
            initial_state = "deferred"
        else:
            initial_state = "pending"

        task = Task(
            id=tid, name=name, vault=vault, operation=operation, params=params,
            priority=priority, schedule=schedule, parent_id=parent_id,
            state=initial_state, created_at=ts, updated_at=ts,
            scheduled_for=scheduled_for, check_limits=check_limits,
        )
        self._tasks[tid] = task
        self.bus.emit("task_updated", {"task_id": tid, "state": task.state})
        if initial_state == "scheduled":
            self.bus.emit(
                "task_scheduled",
                {"task_id": tid, "scheduled_for": scheduled_for},
            )
        if run_now and task.state == "pending":
            self._dispatch(task)
        return task

    def _create_parent_all(
        self,
        name: str,
        operation: str,
        params: dict,
        priority: str,
        schedule: str,
        run_now: bool,
        force: bool = False,
        check_limits: bool = False,
    ) -> Task:
        ts = _utcnow_iso()
        parent_id = f"t-{uuid.uuid4().hex[:12]}"
        data = {
            "name": name, "vault": "ALL", "operation": operation, "params": params,
            "priority": priority, "schedule": schedule, "parent_id": None,
        }
        if check_limits:
            data["check_limits"] = True
        self._append({"ts": ts, "event": "created", "task_id": parent_id, "data": data})
        vault_names = self.list_vault_names()
        self._append({
            "ts": ts, "event": "dispatch_started", "task_id": parent_id,
            "expected_child_count": len(vault_names),
        })
        parent = Task(
            id=parent_id, name=name, vault="ALL", operation=operation, params=params,
            priority=priority, schedule=schedule, state="pending",
            created_at=ts, updated_at=ts, check_limits=check_limits,
        )
        self._tasks[parent_id] = parent
        for vname in vault_names:
            self._create_single(
                f"{name}-{vname}", vname, operation, params, priority, schedule,
                parent_id, run_now, force=force, check_limits=check_limits,
            )
        self.bus.emit("task_updated", {"task_id": parent_id, "state": parent.state})
        return parent

    def _on_window_activated(self, _payload: dict) -> None:
        """Promote deferred high/medium-priority tasks when the window opens."""
        promoted: List[Task] = []
        for tid, task in list(self._tasks.items()):
            if task.state != "deferred":
                continue
            if task.priority == "low":
                continue
            ts = _utcnow_iso()
            self._append({"ts": ts, "event": "promoted", "task_id": tid})
            task.state = "pending"
            task.updated_at = ts
            promoted.append(task)
            self.bus.emit("task_updated", {"task_id": tid, "state": "pending"})
        for task in promoted:
            self._dispatch(task)

    def promote(self, task_id: str) -> Optional[Task]:
        task = self._tasks.get(task_id)
        if not task or task.state not in ("deferred", "scheduled"):
            return None
        ts = _utcnow_iso()
        self._append({"ts": ts, "event": "promoted", "task_id": task_id})
        task.state = "pending"
        task.updated_at = ts
        self.bus.emit("task_updated", {"task_id": task_id, "state": "pending"})
        self._dispatch(task)
        return task

    def cancel(self, task_id: str) -> bool:
        task = self._tasks.get(task_id)
        if not task:
            return False
        if task.state == "running":
            proc = self._procs.get(task_id)
            if proc is not None:
                try:
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                except Exception:
                    log.exception("cancel: terminate/kill failed for %s", task_id)
            ts = _utcnow_iso()
            self._append({"ts": ts, "event": "cancelled", "task_id": task_id})
            task.state = "cancelled"
            task.updated_at = ts
            task.finished_at = ts
            task.pid = None
            self.bus.emit("task_updated", {"task_id": task_id, "state": "cancelled"})
            if task.parent_id:
                self._aggregate_parent(task.parent_id)
            return True
        if task.state in ("pending", "deferred", "scheduled"):
            ts = _utcnow_iso()
            self._append({"ts": ts, "event": "cancelled", "task_id": task_id})
            task.state = "cancelled"
            task.updated_at = ts
            task.finished_at = ts
            self.bus.emit("task_updated", {"task_id": task_id, "state": "cancelled"})
            return True
        return False

    def archive(self, task_id: str) -> bool:
        task = self._tasks.get(task_id)
        if not task or task.state not in ("completed", "failed", "interrupted", "cancelled"):
            return False
        ts = _utcnow_iso()
        self._append({"ts": ts, "event": "archived", "task_id": task_id})
        task.state = "archived"
        task.updated_at = ts
        self.bus.emit("task_updated", {"task_id": task_id, "state": "archived"})
        return True

    # ----- Usage-limit sampling ("check limits" toggle) -----
    def _sample_usage(self, task: Task, phase: str) -> None:
        """Take one claude.ai usage reading for `task` and record it as a
        ``usage_sampled`` event (phase ``before`` | ``after``). No-op when the
        manager has no usage provider (unit tests) — so the run path is
        unaffected for tasks that didn't opt in. Never raises: a failed read is
        stored as a classified error reading so the UI can show *something*."""
        if not self._usage_provider:
            return
        try:
            reading = self._usage_provider() or {}
        except Exception as exc:  # provider must never break task execution
            log.exception("usage sample (%s) failed for %s", phase, task.id)
            reading = {"ok": False, "reason": "sample_error", "error": str(exc)}
        ts = _utcnow_iso()
        self._append({
            "ts": ts, "event": "usage_sampled", "task_id": task.id,
            "phase": phase, "data": reading,
        })
        if phase == "after":
            task.usage_after = reading
        else:
            task.usage_before = reading
        task.updated_at = ts
        self.bus.emit("task_updated", {"task_id": task.id, "state": task.state})

    # ----- Dispatch -----
    def _dispatch(self, task: Task) -> None:
        if task.vault == "ALL":
            return  # parent task — no direct dispatch; children run
        # Run synchronously via the runner (tests can inject a recording runner).
        # Production setups should wrap this in eventlet.spawn() at the call site.
        if self._executor is not None:
            self._executor(task)
            return
        self._execute(task)

    def set_executor(self, fn: Callable[[Task], None]) -> None:
        """Inject an executor (e.g., eventlet.spawn wrapper) for async dispatch."""
        self._executor = fn

    def _execute(self, task: Task) -> None:
        # "check limits": read usage just before the run. This blocks the
        # dispatch greenlet (a claude.ai GET, a few seconds) but not the request
        # that queued the task — dispatch is already async via set_executor.
        if task.check_limits:
            self._sample_usage(task, "before")
        cmd, cwd = self._build_command(task)
        if cmd is None:
            ts = _utcnow_iso()
            self._append({"ts": ts, "event": "started", "task_id": task.id})
            task.state = "running"
            task.started_at = ts
            task.updated_at = ts
            self.bus.emit("task_updated", {"task_id": task.id, "state": "running"})
            self._finalize(task, exit_code=1, error="vault path not found")
            return
        log_file = self.log_dir / f"{task.id}.log"
        log_file.parent.mkdir(parents=True, exist_ok=True)

        # Legacy 3-arg runner path (tests). Emit `started` first so the event
        # ordering matches the streaming path.
        if self._runner is not None:
            ts = _utcnow_iso()
            self._append({"ts": ts, "event": "started", "task_id": task.id})
            task.state = "running"
            task.started_at = ts
            task.updated_at = ts
            self.bus.emit("task_updated", {"task_id": task.id, "state": "running"})
            try:
                rc = self._runner(cmd, cwd, log_file)
            except FileNotFoundError as exc:
                self._finalize(task, exit_code=127, error=f"executable not found: {exc}")
                return
            except Exception as exc:
                self._finalize(task, exit_code=1, error=str(exc))
                return
            if rc == 0:
                self._finalize(task, exit_code=0)
            else:
                self._finalize(task, exit_code=rc, error=f"non-zero exit {rc}")
            return

        # Streaming production runner — emits task_log_appended chunks on the
        # bus while writing the same log file. The `started` event is written
        # after Popen returns so its PID can be recorded; cancellation reaches
        # the live process via _procs[task.id].
        try:
            self._run_streaming(task, cmd, cwd, log_file)
        except FileNotFoundError as exc:
            ts = _utcnow_iso()
            self._append({"ts": ts, "event": "started", "task_id": task.id})
            task.state = "running"
            task.started_at = ts
            task.updated_at = ts
            self.bus.emit("task_updated", {"task_id": task.id, "state": "running"})
            self._finalize(task, exit_code=127, error=f"executable not found: {exc}")
        except Exception as exc:
            ts = _utcnow_iso()
            self._append({"ts": ts, "event": "started", "task_id": task.id})
            task.state = "running"
            task.started_at = ts
            task.updated_at = ts
            self.bus.emit("task_updated", {"task_id": task.id, "state": "running"})
            self._finalize(task, exit_code=1, error=str(exc))

    def _run_streaming(
        self,
        task: Task,
        cmd: List[str],
        cwd: Optional[str],
        log_file: Path,
        max_bytes: Optional[int] = None,
    ) -> None:
        """Spawn cmd in a pseudo-tty, stream output to log_file + bus, finalize.

        We allocate a PTY for the child's stdout/stderr so CLIs that switch to
        block-buffering when stdout is a pipe (claude -p among them) keep
        line-buffering toward what they think is a terminal. Without the PTY
        the live-tail UI would sit idle for the whole task and only show the
        log once the process exited and flushed.

        A dedicated reader thread does the blocking os.read() on the PTY
        master so we don't depend on eventlet patching os-level fds. The
        thread emits task_log_appended chunks and writes the same bytes to
        the log file. The dispatching greenlet/thread waits on proc.wait()
        and joins the reader.
        """
        # Resolve at call time so tests can patch LOG_MAX_BYTES on the module
        # without having to re-import.
        if max_bytes is None:
            max_bytes = LOG_MAX_BYTES

        # Allocate a PTY pair. If it fails (rare; e.g., container with no
        # /dev/ptmx), fall back to a pipe — output won't live-stream from
        # block-buffered children, but the task still runs and the log file
        # captures the eventual flush.
        slave_fd: Optional[int] = None
        master_fd: Optional[int] = None
        try:
            master_fd, slave_fd = pty.openpty()
        except OSError:
            log.warning("pty.openpty() failed; falling back to pipes")

        with log_file.open("wb", buffering=0) as logf:
            header = (
                f"$ {' '.join(shlex.quote(x) for x in cmd)}\n"
                f"cwd: {cwd}\n\n"
            ).encode("utf-8")
            logf.write(header)

            popen_kwargs = dict(
                cwd=cwd, stdin=subprocess.DEVNULL, close_fds=True,
            )
            if master_fd is not None and slave_fd is not None:
                popen_kwargs["stdout"] = slave_fd
                popen_kwargs["stderr"] = slave_fd
                popen_kwargs["start_new_session"] = True  # own pty session
            else:
                popen_kwargs["stdout"] = subprocess.PIPE
                popen_kwargs["stderr"] = subprocess.STDOUT

            try:
                proc = subprocess.Popen(cmd, **popen_kwargs)
            finally:
                if slave_fd is not None:
                    try:
                        os.close(slave_fd)
                    except OSError:
                        pass
            self._procs[task.id] = proc

            ts = _utcnow_iso()
            self._append({
                "ts": ts, "event": "started", "task_id": task.id, "pid": proc.pid,
            })
            task.state = "running"
            task.started_at = ts
            task.updated_at = ts
            task.pid = proc.pid
            self.bus.emit("task_updated", {"task_id": task.id, "state": "running"})

            counter = {"written": 0, "truncated": False}
            log_lock = threading.Lock()

            def emit_chunk(text: str, raw_len: int) -> None:
                with log_lock:
                    if counter["truncated"]:
                        return
                    if counter["written"] >= max_bytes:
                        return
                    try:
                        logf.write(text.encode("utf-8", errors="replace"))
                    except Exception:
                        log.exception("log write failed for %s", task.id)
                    counter["written"] += raw_len
                    self.bus.emit("task_log_appended", {
                        "task_id": task.id, "chunk": text,
                    })
                    if counter["written"] >= max_bytes:
                        marker = (
                            f"\n... [output capped at {max_bytes} bytes; "
                            f"tail discarded]\n"
                        )
                        try:
                            logf.write(marker.encode("utf-8"))
                        except Exception:
                            pass
                        self.bus.emit("task_log_appended", {
                            "task_id": task.id, "chunk": marker,
                        })
                        counter["truncated"] = True

            def pty_reader():
                try:
                    while True:
                        try:
                            chunk = os.read(master_fd, 4096)
                        except OSError:
                            # Slave end closed (child exited) → EIO on Linux.
                            break
                        if not chunk:
                            break
                        emit_chunk(chunk.decode("utf-8", errors="replace"), len(chunk))
                finally:
                    try:
                        os.close(master_fd)
                    except OSError:
                        pass

            def pipe_reader():
                stream = proc.stdout
                if stream is None:
                    return
                try:
                    while True:
                        chunk = stream.read1(4096) if hasattr(stream, "read1") else stream.read(4096)
                        if not chunk:
                            break
                        if isinstance(chunk, bytes):
                            text = chunk.decode("utf-8", errors="replace")
                            raw = len(chunk)
                        else:
                            text = chunk
                            raw = len(chunk.encode("utf-8", errors="replace"))
                        emit_chunk(text, raw)
                finally:
                    try:
                        stream.close()
                    except Exception:
                        pass

            reader_target = pty_reader if master_fd is not None else pipe_reader
            reader = threading.Thread(target=reader_target, daemon=True)
            reader.start()
            rc = proc.wait()
            reader.join(timeout=2)

        # If cancel() already wrote a terminal event, don't overwrite it.
        if task.state == "cancelled":
            self._procs.pop(task.id, None)
            if task.parent_id:
                self._aggregate_parent(task.parent_id)
            return
        if rc == 0:
            self._finalize(task, exit_code=0)
        else:
            self._finalize(task, exit_code=rc, error=f"non-zero exit {rc}")

    def _build_command(self, task: Task) -> tuple[Optional[List[str]], Optional[str]]:
        vault_path = self.get_vault_path(task.vault)
        if vault_path is None:
            return None, None
        op = task.operation
        params = task.params
        if op == "wiki-ingest":
            ingest = str(self.resman_root / "tools" / "ingest.sh")
            cmd = [ingest, vault_path, params["url"]]
            if params.get("update_canvas"):
                cmd.append("--can")
            return cmd, vault_path
        if op == "wiki-ingest-prefix":
            ingest = str(self.resman_root / "tools" / "ingest.sh")
            prefix_file = str(self.resman_root / URL_INGEST_PREFIX_FILE)
            cmd = [ingest, vault_path, params["url"], "--prefix", prefix_file]
            if params.get("update_canvas"):
                cmd.append("--can")
            return cmd, vault_path
        if op == "wiki-lint":
            return ["claude", "-p", plugin_commands.WIKI_LINT, "--dangerously-skip-permissions"], vault_path
        if op == "wiki-autoresearch":
            return [
                "claude", "-p", plugin_commands.autoresearch_prompt(params["topic"]),
                "--dangerously-skip-permissions",
            ], vault_path
        if op == "wiki-canvas":
            return [
                "claude", "-p", plugin_commands.canvas_prompt(params.get("description", "")),
                "--dangerously-skip-permissions",
            ], vault_path
        if op == "wiki-update-hot-cache":
            return [
                "claude", "-p", plugin_commands.WIKI_UPDATE_HOT_CACHE,
                "--dangerously-skip-permissions",
            ], vault_path
        if op == "wiki-hint":
            return [
                "claude", "-p", plugin_commands.WIKI_HINT,
                "--dangerously-skip-permissions",
            ], vault_path
        if op == "wiki-bootstrap":
            repo_root = self.resman_root
            prompt = plugin_commands.new_vault_bootstrap_prompt(
                repo_root / plugin_commands.NEW_VAULT_PREFIX_FILE,
                repo_root / plugin_commands.NEW_VAULT_SUFFIX_FILE,
            )
            return [
                "claude", "-p", prompt,
                "--dangerously-skip-permissions",
            ], vault_path
        if op == "run-prompt":
            return [
                "claude", "-p", params["prompt"], "--dangerously-skip-permissions",
            ], vault_path
        if op == "run-shell":
            cmd_parts = list(params["cmd_parts"])
            return cmd_parts, vault_path
        return None, vault_path

    def build_attend_prompt(self, task: Task) -> Optional[str]:
        """Return the Claude prompt for an interactive re-run, or None.

        Used by the "attend" action: the same prompt that the non-interactive
        `claude -p` task ran with is delivered into an interactive Claude
        REPL via bracketed paste, so the user can answer any prompts that
        the original one-shot run couldn't.

        Returns None for operations that don't drive Claude with a prompt
        (shell wrappers like wiki-ingest, wiki-ingest-prefix, run-shell) —
        those aren't attendable because there's no Claude REPL to attach to.
        """
        op = task.operation
        params = task.params or {}
        if op == "wiki-lint":
            return plugin_commands.WIKI_LINT
        if op == "wiki-autoresearch":
            return plugin_commands.autoresearch_prompt(params.get("topic", ""))
        if op == "wiki-canvas":
            return plugin_commands.canvas_prompt(params.get("description", ""))
        if op == "wiki-update-hot-cache":
            return plugin_commands.WIKI_UPDATE_HOT_CACHE
        if op == "wiki-hint":
            return plugin_commands.WIKI_HINT
        if op == "wiki-bootstrap":
            repo_root = self.resman_root
            return plugin_commands.new_vault_bootstrap_prompt(
                repo_root / plugin_commands.NEW_VAULT_PREFIX_FILE,
                repo_root / plugin_commands.NEW_VAULT_SUFFIX_FILE,
            )
        if op == "run-prompt":
            prompt = params.get("prompt")
            return prompt if isinstance(prompt, str) and prompt else None
        return None

    def _finalize(self, task: Task, exit_code: int, error: Optional[str] = None) -> None:
        # If a cancel raced ahead and already wrote a terminal event, don't
        # overwrite it with completed/failed. Still drop proc tracking and
        # re-aggregate the parent if any.
        if task.state == "cancelled":
            self._procs.pop(task.id, None)
            if task.parent_id:
                self._aggregate_parent(task.parent_id)
            return
        ts = _utcnow_iso()
        if exit_code == 0:
            self._append({"ts": ts, "event": "completed", "task_id": task.id, "exit_code": 0})
            task.state = "completed"
        else:
            self._append({
                "ts": ts, "event": "failed", "task_id": task.id,
                "exit_code": exit_code, "error": error or "",
            })
            task.state = "failed"
            task.error = error
        task.exit_code = exit_code
        task.finished_at = ts
        task.updated_at = ts
        task.pid = None
        self._procs.pop(task.id, None)
        self.bus.emit("task_updated", {"task_id": task.id, "state": task.state})
        # "check limits": read usage again now the run is done. Paired with the
        # `before` reading so the UI can show both points and the delta.
        if task.check_limits:
            self._sample_usage(task, "after")
        if task.parent_id:
            self._aggregate_parent(task.parent_id)

    def _aggregate_parent(self, parent_id: str) -> None:
        parent = self._tasks.get(parent_id)
        if not parent:
            return
        children = [t for t in self._tasks.values() if t.parent_id == parent_id]
        if not children:
            return
        if any(c.state == "running" for c in children):
            new = "running"
        elif any(c.state == "failed" for c in children):
            new = "failed"
        elif all(c.state == "completed" for c in children):
            new = "completed"
        else:
            return
        if parent.state != new:
            ts = _utcnow_iso()
            ev = "completed" if new == "completed" else ("failed" if new == "failed" else "started")
            payload = {"ts": ts, "event": ev, "task_id": parent_id}
            if ev == "completed":
                payload["exit_code"] = 0
            elif ev == "failed":
                payload["exit_code"] = 1
            self._append(payload)
            parent.state = new
            parent.updated_at = ts
            self.bus.emit("task_updated", {"task_id": parent_id, "state": new})
            self.bus.emit("child_state_changed", {"parent_id": parent_id, "child_id": None, "state": new})

    def cron_skipped(self, cron_name: str, scheduled_at: str, skip_count: int) -> None:
        ts = _utcnow_iso()
        self._append({
            "ts": ts, "event": "cron_skipped", "task_id": f"cron:{cron_name}",
            "cron_name": cron_name, "scheduled_at": scheduled_at,
            "skip_reason": "window_inactive", "skip_count": skip_count,
        })

    # ----- Listing / log -----
    def list(
        self,
        vault: Optional[str] = None,
        priority: Optional[str] = None,
        state: Optional[str] = None,
        include_archived: bool = False,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> List[dict]:
        items = list(self._tasks.values())
        if not include_archived:
            items = [t for t in items if t.state != "archived"]
        if vault:
            items = [t for t in items if t.vault == vault or t.parent_id is not None and self._tasks.get(t.parent_id) and self._tasks[t.parent_id].vault == vault]
        if priority:
            items = [t for t in items if t.priority == priority]
        if state:
            items = [t for t in items if t.state == state]
        items.sort(key=lambda t: t.created_at, reverse=True)
        if offset:
            items = items[offset:]
        if limit is not None:
            items = items[:limit]
        return [t.to_dict() for t in items]

    def get(self, task_id: str) -> Optional[Task]:
        return self._tasks.get(task_id)

    def read_log(self, task_id: str) -> str:
        path = self.log_dir / f"{task_id}.log"
        if not path.exists():
            return ""
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    # ----- Compaction -----
    def compact(self) -> dict:
        """Compact tasks.jsonl by snapshotting old terminal-state tasks."""
        if not self.log_path.exists():
            return {"compacted": 0}
        now = datetime.now(timezone.utc)
        snapshots = []
        keep_lines: List[str] = []
        with self.log_path.open("r", encoding="utf-8") as f:
            lines = f.readlines()
        # Group events by task_id
        per_task: Dict[str, List[dict]] = {}
        order: List[str] = []
        for raw in lines:
            line = raw.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                keep_lines.append(raw)
                continue
            tid = ev.get("task_id")
            if tid is None:
                keep_lines.append(raw)
                continue
            if tid not in per_task:
                order.append(tid)
                per_task[tid] = []
            per_task[tid].append(ev)
        compacted = 0
        for tid in order:
            evs = per_task[tid]
            task = self._tasks.get(tid)
            if not task or task.state not in ("completed", "failed", "archived", "interrupted", "cancelled"):
                for ev in evs:
                    keep_lines.append(json.dumps(ev, separators=(",", ":")) + "\n")
                continue
            # Age check: compact if updated > 90 days ago
            try:
                updated_ts = task.updated_at.replace("Z", "+00:00")
                last = datetime.fromisoformat(updated_ts) if updated_ts else now
            except ValueError:
                last = now
            age_days = (now - last).total_seconds() / 86400
            if age_days < 90:
                for ev in evs:
                    keep_lines.append(json.dumps(ev, separators=(",", ":")) + "\n")
                continue
            snap = {
                "ts": _utcnow_iso(), "event": "snapshot", "task_id": tid,
                "data": task.to_dict(),
            }
            snapshots.append(json.dumps(snap, separators=(",", ":")) + "\n")
            compacted += 1
        with self._write_lock:
            with self.log_path.open("w", encoding="utf-8") as f:
                f.writelines(snapshots)
                f.writelines(keep_lines)
        return {"compacted": compacted}


def _default_runner(cmd: List[str], cwd: Optional[str], log_file: Path) -> int:
    """Default subprocess runner. Argument-list form; never shell=True."""
    with log_file.open("w", encoding="utf-8") as logf:
        logf.write(f"$ {' '.join(shlex.quote(x) for x in cmd)}\n")
        logf.write(f"cwd: {cwd}\n\n")
        logf.flush()
        proc = subprocess.Popen(
            cmd, cwd=cwd, stdout=logf, stderr=subprocess.STDOUT,
        )
        try:
            return proc.wait(timeout=3600)
        except subprocess.TimeoutExpired:
            proc.kill()
            return 124
