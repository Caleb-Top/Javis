"""Fail-soft single-writer service for governed environment state."""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable, Collection
from dataclasses import dataclass, field
from typing import Any

from core.environment.contracts import (
    MAX_FACTS,
    EnvironmentObservationV1,
    EnvironmentSnapshotV1,
    SourceHealth,
    SourceKind,
)
from core.environment.state import EnvironmentReducer


logger = logging.getLogger("jarvis.environment")

DEFAULT_QUEUE_CAPACITY = 256
DEFAULT_CALL_TIMEOUT_SECONDS = 0.5


@dataclass(frozen=True, slots=True)
class EnvironmentServiceStatus:
    state: str
    accepting: bool
    queue_depth: int
    accepted_commands: int
    rejected_commands: int
    failure_count: int
    last_error_code: str | None
    snapshot_revision: int


@dataclass(slots=True)
class _Command:
    kind: str
    payload: tuple[Any, ...] = ()
    completed: threading.Event | None = None
    result: Any = None
    error: BaseException | None = None


_STOP = object()


class EnvironmentService:
    """Own the sole mutation path to one :class:`EnvironmentReducer`.

    Producers use the non-blocking ``submit_observation`` path. Synchronous
    management calls are bounded and return a fail-soft result instead of
    propagating L4 failures into conversation or life services.
    """

    name = "environment"

    def __init__(
        self,
        runtime_boot_id: str,
        *,
        now: Callable[[], float] | None = None,
        queue_capacity: int = DEFAULT_QUEUE_CAPACITY,
        max_facts: int = MAX_FACTS,
        reducer: EnvironmentReducer | None = None,
    ) -> None:
        if type(queue_capacity) is not int or not 1 <= queue_capacity <= 65_536:
            raise ValueError("queue_capacity must be between 1 and 65536")
        self._reducer = reducer or EnvironmentReducer(
            runtime_boot_id,
            now=now,
            max_facts=max_facts,
        )
        if self._reducer.runtime_boot_id != runtime_boot_id:
            raise ValueError("reducer runtime boot does not match service")
        self._queue: queue.Queue[_Command | object] = queue.Queue(
            maxsize=queue_capacity
        )
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._state = "new"
        self._accepting = False
        self._accepted_commands = 0
        self._rejected_commands = 0
        self._failure_count = 0
        self._last_error_code: str | None = None
        self._latest_snapshot = self._reducer.snapshot()

    @property
    def runtime_boot_id(self) -> str:
        return self._reducer.runtime_boot_id

    @property
    def latest_snapshot(self) -> EnvironmentSnapshotV1:
        with self._lock:
            return self._latest_snapshot

    @property
    def status(self) -> EnvironmentServiceStatus:
        with self._lock:
            return EnvironmentServiceStatus(
                state=self._state,
                accepting=self._accepting,
                queue_depth=self._queue.qsize(),
                accepted_commands=self._accepted_commands,
                rejected_commands=self._rejected_commands,
                failure_count=self._failure_count,
                last_error_code=self._last_error_code,
                snapshot_revision=self._latest_snapshot.revision,
            )

    def start(self) -> bool:
        with self._lock:
            if self._state == "running":
                return False
            if self._state != "new":
                return False
            self._state = "running"
            self._accepting = True
            thread = threading.Thread(
                target=self._run,
                name="javis-environment-writer",
                daemon=True,
            )
            self._thread = thread
            thread.start()
            return True

    def submit_observation(self, observation: EnvironmentObservationV1) -> bool:
        """Queue an observation without waiting or raising into its caller."""

        if not isinstance(observation, EnvironmentObservationV1):
            self._reject()
            return False
        return self._enqueue(_Command("observation", (observation,)))

    def ingest(
        self,
        observation: EnvironmentObservationV1,
        *,
        timeout: float = DEFAULT_CALL_TIMEOUT_SECONDS,
    ) -> bool:
        command = self._call("observation", (observation,), timeout=timeout)
        return bool(command is not None and command.error is None and command.result)

    def set_source_health(
        self,
        source_kind: SourceKind | str,
        health: SourceHealth | str,
        *,
        timeout: float = DEFAULT_CALL_TIMEOUT_SECONDS,
    ) -> bool:
        command = self._call(
            "source_health", (source_kind, health), timeout=timeout
        )
        return bool(command is not None and command.error is None and command.result)

    def set_permission_revision(
        self,
        revision: int,
        *,
        invalidated_grant_id_hashes: Collection[str] = (),
        invalidated_sources: Collection[SourceKind | str] = (),
        timeout: float = DEFAULT_CALL_TIMEOUT_SECONDS,
    ) -> bool:
        command = self._call(
            "permission",
            (
                revision,
                tuple(invalidated_grant_id_hashes),
                tuple(invalidated_sources),
            ),
            timeout=timeout,
        )
        return bool(command is not None and command.error is None and command.result)

    def snapshot(
        self, *, timeout: float = DEFAULT_CALL_TIMEOUT_SECONDS
    ) -> EnvironmentSnapshotV1:
        command = self._call("snapshot", timeout=timeout)
        if command is not None and command.error is None:
            return command.result
        return self.latest_snapshot

    def reset(self, *, timeout: float = DEFAULT_CALL_TIMEOUT_SECONDS) -> bool:
        command = self._call("reset", timeout=timeout)
        return bool(command is not None and command.error is None and command.result)

    def stop(
        self,
        *,
        drain: bool = True,
        timeout: float = 2.0,
    ) -> bool:
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout < 0:
            raise ValueError("timeout must be a non-negative number")
        with self._lock:
            if self._state in {"stopped", "new"}:
                if self._state == "new":
                    self._state = "stopped"
                self._accepting = False
                return False
            if self._state == "stopping":
                thread = self._thread
            else:
                self._state = "stopping"
                self._accepting = False
                thread = self._thread
        if not drain:
            self._discard_pending()
        deadline = time.monotonic() + float(timeout)
        inserted = False
        while not inserted:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                self._queue.put(_STOP, timeout=min(remaining, 0.05))
                inserted = True
            except queue.Full:
                continue
        if thread is not None:
            thread.join(max(0.0, deadline - time.monotonic()))
        with self._lock:
            stopped = thread is None or not thread.is_alive()
            if stopped:
                self._state = "stopped"
                self._thread = None
            return stopped

    close = stop

    def _call(
        self,
        kind: str,
        payload: tuple[Any, ...] = (),
        *,
        timeout: float,
    ) -> _Command | None:
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout < 0:
            raise ValueError("timeout must be a non-negative number")
        completed = threading.Event()
        command = _Command(kind, payload, completed)
        if not self._enqueue(command):
            return None
        if not completed.wait(float(timeout)):
            self._reject()
            return None
        return command

    def _enqueue(self, command: _Command) -> bool:
        with self._lock:
            if not self._accepting or self._state != "running":
                self._rejected_commands += 1
                return False
        try:
            self._queue.put_nowait(command)
        except queue.Full:
            self._reject()
            return False
        with self._lock:
            self._accepted_commands += 1
        return True

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            try:
                if item is _STOP:
                    return
                assert isinstance(item, _Command)
                self._execute(item)
            finally:
                self._queue.task_done()

    def _execute(self, command: _Command) -> None:
        try:
            if command.kind == "observation":
                command.result = self._reducer.reduce(command.payload[0])
            elif command.kind == "source_health":
                command.result = self._reducer.set_source_health(*command.payload)
            elif command.kind == "permission":
                revision, hashes, sources = command.payload
                command.result = self._reducer.set_permission_revision(
                    revision,
                    invalidated_grant_id_hashes=hashes,
                    invalidated_sources=sources,
                )
            elif command.kind == "snapshot":
                command.result = self._reducer.snapshot()
            elif command.kind == "reset":
                command.result = self._reducer.reset()
            else:
                raise ValueError("unsupported environment command")
            snapshot = (
                command.result
                if isinstance(command.result, EnvironmentSnapshotV1)
                else self._reducer.snapshot()
            )
            with self._lock:
                self._latest_snapshot = snapshot
                self._last_error_code = None
        except Exception as exc:
            command.error = exc
            self._record_failure(exc)
            self._degrade_failed_source(command)
        finally:
            if command.completed is not None:
                command.completed.set()

    def _degrade_failed_source(self, command: _Command) -> None:
        if command.kind != "observation" or not command.payload:
            return
        observation = command.payload[0]
        if not isinstance(observation, EnvironmentObservationV1):
            return
        try:
            self._reducer.set_source_health(
                observation.source_kind, SourceHealth.DEGRADED
            )
            snapshot = self._reducer.snapshot()
        except Exception:
            return
        with self._lock:
            self._latest_snapshot = snapshot

    def _record_failure(self, error: BaseException) -> None:
        code = type(error).__name__
        with self._lock:
            self._failure_count += 1
            self._last_error_code = code
        logger.warning("Environment command failed: %s", code)

    def _reject(self) -> None:
        with self._lock:
            self._rejected_commands += 1

    def _discard_pending(self) -> None:
        while True:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                return
            try:
                if isinstance(item, _Command):
                    item.error = RuntimeError("environment service stopped")
                    if item.completed is not None:
                        item.completed.set()
            finally:
                self._queue.task_done()


__all__ = [
    "DEFAULT_CALL_TIMEOUT_SECONDS",
    "DEFAULT_QUEUE_CAPACITY",
    "EnvironmentService",
    "EnvironmentServiceStatus",
]
