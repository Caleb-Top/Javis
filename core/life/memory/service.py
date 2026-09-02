"""Bounded single-writer runtime service for autobiographical memory."""

from __future__ import annotations

import itertools
import queue
import threading
import time
from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, TypeVar

from .contracts import (
    ConfirmSharedMemory,
    CorrectMemory,
    DeletionRequest,
    DerivationEdge,
    ExperienceEpisode,
    ForgetMemory,
    JournalEntry,
    MemoryItemKind,
    MigrateLegacyBatch,
    ProposeSharedMemory,
    RecallBundle,
    RecallQuery,
    RejectSharedMemory,
    RelationshipEvent,
    RevokeSharedMemory,
    SessionParticipant,
    SharedMemory,
    Subject,
    UserModelClaim,
)
from .deletion import DeletionWorker
from .migration import LegacyMemoryCandidate
from .extraction import EpisodeExtractor, JournalProjection
from .projection import TerminalProjector
from .recall import RecallEngine, empty_recall_bundle
from .store import MemoryStore


_T = TypeVar("_T")
_STOPPED_STATES = frozenset({"stopping", "stopped"})


class MemoryServiceError(RuntimeError):
    """Base class for stable MemoryService failures."""

    reason_code = "memory_service_error"


class MemoryServiceUnavailable(MemoryServiceError):
    """Raised when a mutation cannot be accepted by the memory service."""

    reason_code = "memory_unavailable"

    def __init__(self, reason_code: str | None = None) -> None:
        self.reason_code = reason_code or self.reason_code
        super().__init__(self.reason_code)


class MemoryQueueFullError(MemoryServiceError):
    """Raised when a critical mutation cannot enter the bounded queue."""

    reason_code = "memory_queue_full"


class MemoryServiceStoppedError(MemoryServiceError):
    """Raised when work is submitted after shutdown starts."""

    reason_code = "memory_service_stopped"


class MemoryCommandPriority(IntEnum):
    CRITICAL = 0
    NORMAL = 10
    BACKGROUND = 20


class _ConversationEvidenceStore(Protocol):
    def scan_terminal_events(self, after_row_id: int = 0, limit: int = 100) -> dict[str, Any]: ...

    def read_request_evidence(self, session_id: str, request_id: str) -> dict[str, Any]: ...

    def redact_request_evidence(
        self, session_id: str, request_id: str, deletion_id: str
    ) -> Mapping[str, Any]: ...


@dataclass(order=True, slots=True)
class _QueuedCommand:
    priority: int
    sequence: int
    name: str = field(compare=False)
    operation: Callable[[MemoryStore, object], Any] = field(compare=False, repr=False)
    future: Future[Any] = field(compare=False, repr=False)


class MemoryService:
    """Own the only MemoryStore writer and fail soft when memory is unavailable.

    Public mutation methods accept immutable domain contracts and return futures.
    There is deliberately no callback or SQL submission API.
    """

    def __init__(
        self,
        data_root: str | Path,
        *,
        conversation_store: _ConversationEvidenceStore | None = None,
        queue_capacity: int = 256,
        read_workers: int = 2,
        read_queue_capacity: int = 8,
        reconcile_interval_seconds: float = 5.0,
        reconcile_page_size: int = 100,
        deletion_cache_capacity: int = 128,
        critical_enqueue_timeout: float = 0.05,
        store_factory: Callable[..., MemoryStore] = MemoryStore,
        terminal_projector: TerminalProjector | None = None,
        episode_extractor: EpisodeExtractor | None = None,
        recall_cache_capacity: int = 128,
        deletion_stage_hook: Callable[[str, str], None] | None = None,
    ) -> None:
        if type(queue_capacity) is not int or queue_capacity < 1:
            raise ValueError("queue_capacity must be a positive integer")
        if type(read_workers) is not int or read_workers < 1 or read_workers > 16:
            raise ValueError("read_workers must be between 1 and 16")
        if type(read_queue_capacity) is not int or read_queue_capacity < 0:
            raise ValueError("read_queue_capacity must be non-negative")
        if reconcile_interval_seconds <= 0:
            raise ValueError("reconcile_interval_seconds must be positive")
        if type(reconcile_page_size) is not int or not 1 <= reconcile_page_size <= 500:
            raise ValueError("reconcile_page_size must be between 1 and 500")
        if type(deletion_cache_capacity) is not int or deletion_cache_capacity < 1:
            raise ValueError("deletion_cache_capacity must be positive")
        if critical_enqueue_timeout < 0:
            raise ValueError("critical_enqueue_timeout must be non-negative")

        self.data_root = Path(data_root).expanduser().resolve()
        self._conversation_store = conversation_store
        self._queue_capacity = queue_capacity
        self._queue: queue.PriorityQueue[_QueuedCommand] = queue.PriorityQueue(
            maxsize=queue_capacity
        )
        self._sequence = itertools.count()
        self._read_workers = read_workers
        self._read_slots = threading.BoundedSemaphore(read_workers + read_queue_capacity)
        self._read_executor: ThreadPoolExecutor | None = None
        self._read_pending = 0
        self._reconcile_interval = float(reconcile_interval_seconds)
        self._reconcile_page_size = reconcile_page_size
        self._deletion_cache_capacity = deletion_cache_capacity
        self._deletion_cache: OrderedDict[str, Future[Any]] = OrderedDict()
        self._critical_enqueue_timeout = float(critical_enqueue_timeout)
        self._store_factory = store_factory
        self._terminal_projector = terminal_projector or TerminalProjector()
        self._episode_extractor = episode_extractor or EpisodeExtractor()
        self._recall_engine = RecallEngine(cache_capacity=recall_cache_capacity)
        self._deletion_stage_hook = deletion_stage_hook
        self._prompt_invalidators: list[Callable[[], None]] = []

        self._lock = threading.RLock()
        self._writer_token = object()
        self._writer_thread: threading.Thread | None = None
        self._store: MemoryStore | None = None
        self._startup_complete = threading.Event()
        self._stop_requested = threading.Event()
        self._accepting = False
        self._state = "new"
        self._reason_code: str | None = None
        self._last_error_reason: str | None = None
        self._metrics = {
            "commands_completed": 0,
            "commands_failed": 0,
            "critical_rejected": 0,
            "background_dropped": 0,
            "reads_rejected": 0,
            "terminal_scanned": 0,
            "terminal_advanced": 0,
            "terminal_pending": 0,
            "terminal_projected": 0,
            "terminal_not_selected": 0,
            "reconcile_failures": 0,
            "deletions_resumed": 0,
            "deletions_verified": 0,
        }

    def start(self, *, timeout: float = 5.0) -> "MemoryService":
        """Start the writer and return even when the store starts degraded."""

        if timeout <= 0:
            raise ValueError("timeout must be positive")
        with self._lock:
            if self._state not in {"new"}:
                return self
            self._state = "starting"
            self._read_executor = ThreadPoolExecutor(
                max_workers=self._read_workers,
                thread_name_prefix="javis-memory-read",
            )
            self._writer_thread = threading.Thread(
                target=self._writer_main,
                name="javis-memory-writer",
                daemon=True,
            )
            self._writer_thread.start()
        if not self._startup_complete.wait(timeout):
            with self._lock:
                self._state = "degraded"
                self._reason_code = "startup_timeout"
                self._accepting = False
            return self
        return self

    def status(self) -> dict[str, Any]:
        """Return content-free service diagnostics."""

        with self._lock:
            store = self._store
            writer = self._writer_thread
            result: dict[str, Any] = {
                "state": self._state,
                "reason_code": self._reason_code,
                "last_error_reason": self._last_error_reason,
                "accepting": self._accepting,
                "queue_depth": self._queue.qsize(),
                "queue_capacity": self._queue_capacity,
                "writer_alive": bool(writer and writer.is_alive()),
                "writer_thread_id": writer.ident if writer is not None else None,
                "read_workers": self._read_workers,
                "read_pending": self._read_pending,
                "deletion_cache_size": len(self._deletion_cache),
                "metrics": dict(self._metrics),
            }
        if store is None:
            result["store"] = {
                "state": "unavailable",
                "schema_version": 0,
                "terminal_cursor": 0,
                "acl_epoch": 0,
                "index_generation": 0,
            }
            return result
        try:
            store_status = store.status()
            metadata = store.metadata()
            result["store"] = {
                "state": store_status.get("state", "degraded"),
                "reason_code": store_status.get("reason_code"),
                "read_only": bool(store_status.get("read_only", True)),
                "fts_available": bool(store_status.get("fts_available", False)),
                "schema_version": int(store_status.get("schema_version", 0)),
                "terminal_cursor": int(metadata.get("terminal_cursor", 0)),
                "acl_epoch": int(metadata.get("acl_epoch", 0)),
                "index_generation": int(metadata.get("index_generation", 0)),
            }
        except Exception:
            result["store"] = {
                "state": "degraded",
                "reason_code": "status_unavailable",
                "schema_version": 0,
                "terminal_cursor": 0,
                "acl_epoch": 0,
                "index_generation": 0,
            }
        return result

    def put_subject(self, subject: Subject) -> Future[bool]:
        return self._submit_store_mutation("put_subject", subject)

    def put_session_participant(self, participant: SessionParticipant) -> Future[bool]:
        return self._submit_store_mutation("put_session_participant", participant)

    def put_item(
        self,
        item: ExperienceEpisode | JournalEntry | SharedMemory | UserModelClaim | RelationshipEvent,
        *,
        acl: Mapping[str, tuple[str, ...] | list[str]] | None = None,
    ) -> Future[bool]:
        frozen_acl = None if acl is None else {str(key): tuple(value) for key, value in acl.items()}
        return self._submit_store_mutation("put_item", item, acl=frozen_acl)

    def propose_shared_memory(self, command: ProposeSharedMemory) -> Future[SharedMemory]:
        return self._submit_shared_transition("propose_shared_memory", command)

    def confirm_shared_memory(self, command: ConfirmSharedMemory) -> Future[SharedMemory]:
        return self._submit_shared_transition("confirm_shared_memory", command)

    def reject_shared_memory(self, command: RejectSharedMemory) -> Future[SharedMemory]:
        return self._submit_shared_transition("reject_shared_memory", command)

    def revoke_shared_memory(self, command: RevokeSharedMemory) -> Future[SharedMemory]:
        return self._submit_shared_transition("revoke_shared_memory", command)

    def correct_memory(
        self, command: CorrectMemory
    ) -> Future[ExperienceEpisode | JournalEntry]:
        if not isinstance(command, CorrectMemory):
            raise TypeError("command must be CorrectMemory")

        def operation(
            store: MemoryStore, writer_token: object
        ) -> ExperienceEpisode | JournalEntry:
            result = store.correct_memory(command, writer_token=writer_token)
            self._invalidate_memory_contexts()
            return result

        return self._submit_write(
            "correct_memory",
            operation,
            priority=MemoryCommandPriority.CRITICAL,
            critical=True,
        )

    def delete_item(self, item_kind: MemoryItemKind | str, item_id: str) -> Future[bool]:
        return self._submit_store_mutation(
            "delete_item",
            item_kind,
            item_id,
            priority=MemoryCommandPriority.CRITICAL,
        )

    def put_derivation_edge(self, edge: DerivationEdge) -> Future[bool]:
        return self._submit_store_mutation("put_derivation_edge", edge)

    def submit_deletion(self, request: DeletionRequest) -> Future[bool]:
        """Queue a deletion state update with bounded idempotency caching."""

        if not isinstance(request, DeletionRequest):
            raise TypeError("request must be a DeletionRequest")
        key = request.deletion_request_id
        with self._lock:
            cached = self._deletion_cache.get(key)
            if cached is not None:
                self._deletion_cache.move_to_end(key)
                return cached  # type: ignore[return-value]
            future = self._submit_store_mutation(
                "put_deletion_request",
                request,
                priority=MemoryCommandPriority.CRITICAL,
            )
            self._deletion_cache[key] = future
            self._deletion_cache.move_to_end(key)
            while len(self._deletion_cache) > self._deletion_cache_capacity:
                self._deletion_cache.popitem(last=False)
        future.add_done_callback(
            lambda completed, deletion_id=key: self._discard_failed_deletion(
                deletion_id, completed
            )
        )
        return future

    def forget_memory(self, command: ForgetMemory) -> Future[dict[str, Any]]:
        if not isinstance(command, ForgetMemory):
            raise TypeError("command must be ForgetMemory")

        def operation(store: MemoryStore, writer_token: object) -> dict[str, Any]:
            result = self._deletion_worker(store, writer_token).execute(command)
            if result.get("state") == "verified":
                with self._lock:
                    self._metrics["deletions_verified"] += 1
            return result

        return self._submit_write(
            "forget_memory",
            operation,
            priority=MemoryCommandPriority.CRITICAL,
            critical=True,
        )

    def deletion_status(self, deletion_request_id: str) -> Future[dict[str, Any] | None]:
        return self._submit_read("deletion_status", None, deletion_request_id)

    def migrate_legacy_batch(
        self,
        command: MigrateLegacyBatch,
        candidates: tuple[LegacyMemoryCandidate, ...] | list[LegacyMemoryCandidate],
    ) -> Future[dict[str, Any]]:
        if not isinstance(command, MigrateLegacyBatch):
            raise TypeError("command must be MigrateLegacyBatch")
        frozen = tuple(candidates)
        if not frozen or any(not isinstance(item, LegacyMemoryCandidate) for item in frozen):
            raise TypeError("candidates must contain LegacyMemoryCandidate values")
        wires = tuple(item.to_dict() for item in frozen)
        return self._submit_store_mutation(
            "migrate_legacy_batch",
            command,
            wires,
            priority=MemoryCommandPriority.CRITICAL,
        )

    def legacy_migration_status(self, migration_id: str) -> Future[dict[str, Any]]:
        fallback = {
            "migration_id": migration_id,
            "batch_count": 0,
            "copied_count": 0,
            "candidate_count": 0,
            "quarantined_count": 0,
            "active_count": 0,
            "fts_visible_count": 0,
        }
        return self._submit_read("legacy_migration_status", fallback, migration_id)

    def register_prompt_invalidator(self, invalidator: Callable[[], None]) -> None:
        if not callable(invalidator):
            raise TypeError("invalidator must be callable")
        with self._lock:
            if invalidator not in self._prompt_invalidators:
                self._prompt_invalidators.append(invalidator)

    def record_terminal_receipt(self, **values: Any) -> Future[dict[str, Any]]:
        frozen = dict(values)
        return self._submit_store_mutation(
            "record_terminal_receipt",
            priority=MemoryCommandPriority.NORMAL,
            **frozen,
        )

    def set_source_progress(self, source_store_id: str, terminal_cursor: int) -> Future[None]:
        return self._submit_store_mutation(
            "set_source_progress",
            source_store_id,
            terminal_cursor,
            priority=MemoryCommandPriority.CRITICAL,
        )

    def put_journal_projection(self, projection: JournalProjection) -> Future[dict[str, Any]]:
        if not isinstance(projection, JournalProjection):
            raise TypeError("projection must be a JournalProjection")
        return self._submit_store_mutation(
            "put_journal_projection",
            projection.entry,
            projection.derivation_edges,
            priority=MemoryCommandPriority.NORMAL,
        )

    def get_subject(self, subject_id: str) -> Future[Subject | None]:
        return self._submit_read("get_subject", None, subject_id)

    def active_session_participants(
        self, session_id: str
    ) -> Future[tuple[SessionParticipant, ...]]:
        return self._submit_read("active_session_participants", (), session_id)

    def get_terminal_receipt(
        self, source_store_id: str, session_id: str, request_id: str
    ) -> Future[dict[str, Any] | None]:
        return self._submit_read(
            "get_terminal_receipt", None, source_store_id, session_id, request_id
        )

    def get_item(self, item_kind: MemoryItemKind | str, item_id: str, access_context: Any) -> Future[Any]:
        return self._submit_read("get_item", None, item_kind, item_id, access_context)

    def search_items(self, query: RecallQuery) -> Future[tuple[Any, ...]]:
        """Fail closed to an empty tuple when memory or the bounded read lane is unavailable."""

        return self._submit_read("search_items", (), query)

    def recall(self, query: RecallQuery) -> Future[RecallBundle]:
        if not isinstance(query, RecallQuery):
            raise TypeError("query must be a RecallQuery")
        fallback = empty_recall_bundle(query, "memory_unavailable")
        return self._submit_read_operation(
            lambda store: self._recall_engine.recall(store, query),
            fallback,
        )

    def reconcile_once(self) -> Future[dict[str, int]]:
        """Run one durable terminal scan on the writer thread."""

        return self._submit_write(
            "terminal_reconcile",
            lambda store, token: self._reconcile_terminal_page(store, token),
            priority=MemoryCommandPriority.CRITICAL,
            critical=True,
        )

    def wake_reconcile(self) -> bool:
        """Best-effort low-latency wakeup; the durable timer recovers drops."""

        try:
            self._submit_write(
                "terminal_wakeup",
                lambda store, token: self._reconcile_terminal_page(store, token),
                priority=MemoryCommandPriority.BACKGROUND,
                critical=False,
            )
            return True
        except MemoryServiceError:
            return False

    def drain(self, *, timeout: float = 5.0) -> bool:
        """Wait for accepted mutations to reach a terminal future state."""

        deadline = time.monotonic() + max(0.0, timeout)
        while self._queue.unfinished_tasks:
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.005)
        return True

    def shutdown(self, *, timeout: float = 5.0, drain: bool = True) -> bool:
        """Stop admission, drain accepted writes, and close on the owner thread."""

        with self._lock:
            if self._state == "stopped":
                return True
            if self._state == "new":
                self._state = "stopped"
                self._reason_code = None
                return True
            self._accepting = False
            self._state = "stopping"
        drained = self.drain(timeout=timeout) if drain else True
        self._stop_requested.set()
        writer = self._writer_thread
        if writer is not None:
            writer.join(timeout=max(0.0, timeout))
        executor = self._read_executor
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)
        self._recall_engine.clear()
        stopped = writer is None or not writer.is_alive()
        with self._lock:
            if stopped:
                self._state = "stopped"
                self._reason_code = None
            else:
                self._state = "degraded"
                self._reason_code = "shutdown_timeout"
        return bool(drained and stopped)

    close = shutdown

    def _submit_store_mutation(
        self,
        method_name: str,
        *args: Any,
        priority: MemoryCommandPriority = MemoryCommandPriority.NORMAL,
        **kwargs: Any,
    ) -> Future[Any]:
        def operation(store: MemoryStore, writer_token: object) -> Any:
            method = getattr(store, method_name)
            return method(*args, writer_token=writer_token, **kwargs)

        return self._submit_write(
            method_name,
            operation,
            priority=priority,
            critical=True,
        )

    def _submit_shared_transition(self, method_name: str, command: Any) -> Future[SharedMemory]:
        def operation(store: MemoryStore, writer_token: object) -> SharedMemory:
            result = getattr(store, method_name)(command, writer_token=writer_token)
            self._recall_engine.clear()
            return result

        return self._submit_write(
            method_name,
            operation,
            priority=MemoryCommandPriority.CRITICAL,
            critical=True,
        )

    def _deletion_worker(
        self, store: MemoryStore, writer_token: object
    ) -> DeletionWorker:
        with self._lock:
            invalidators = tuple(self._prompt_invalidators)
        return DeletionWorker(
            store,
            writer_token,
            conversation_store=self._conversation_store,
            cache_invalidator=self._recall_engine.clear,
            prompt_invalidators=invalidators,
            after_stage=self._deletion_stage_hook,
        )

    def _invalidate_memory_contexts(self) -> None:
        self._recall_engine.clear()
        with self._lock:
            invalidators = tuple(self._prompt_invalidators)
        for invalidate in invalidators:
            invalidate()

    def _discard_failed_deletion(
        self, deletion_id: str, completed: Future[Any]
    ) -> None:
        if not completed.cancelled() and completed.exception() is None:
            return
        with self._lock:
            if self._deletion_cache.get(deletion_id) is completed:
                self._deletion_cache.pop(deletion_id, None)

    def _submit_write(
        self,
        name: str,
        operation: Callable[[MemoryStore, object], _T],
        *,
        priority: MemoryCommandPriority,
        critical: bool,
    ) -> Future[_T]:
        with self._lock:
            state = self._state
            accepting = self._accepting
            reason = self._reason_code
            if state in _STOPPED_STATES:
                raise MemoryServiceStoppedError()
            if not accepting:
                raise MemoryServiceUnavailable(reason)
            future: Future[_T] = Future()
            command = _QueuedCommand(
                int(priority), next(self._sequence), name, operation, future
            )
            try:
                self._queue.put(
                    command,
                    block=critical,
                    timeout=self._critical_enqueue_timeout if critical else None,
                )
            except queue.Full as exc:
                metric = "critical_rejected" if critical else "background_dropped"
                self._metrics[metric] += 1
                if critical:
                    raise MemoryQueueFullError() from exc
                raise MemoryServiceError("background_queue_full") from exc
        return future

    def _submit_read(self, method_name: str, fallback: _T, *args: Any) -> Future[_T]:
        return self._submit_read_operation(
            lambda store: getattr(store, method_name)(*args),
            fallback,
        )

    def _submit_read_operation(
        self,
        operation: Callable[[MemoryStore], _T],
        fallback: _T,
    ) -> Future[_T]:
        with self._lock:
            store = self._store
            executor = self._read_executor
            ready = self._state == "ready"
        if store is None or executor is None or not ready:
            return _resolved_future(fallback)
        if not self._read_slots.acquire(blocking=False):
            with self._lock:
                self._metrics["reads_rejected"] += 1
            return _resolved_future(fallback)
        with self._lock:
            self._read_pending += 1

        def read() -> _T:
            try:
                return operation(store)
            except Exception:
                with self._lock:
                    self._last_error_reason = "memory_read_failed"
                return fallback
            finally:
                with self._lock:
                    self._read_pending -= 1
                self._read_slots.release()

        try:
            return executor.submit(read)
        except RuntimeError:
            with self._lock:
                self._read_pending -= 1
                self._metrics["reads_rejected"] += 1
            self._read_slots.release()
            return _resolved_future(fallback)

    def _writer_main(self) -> None:
        store: MemoryStore | None = None
        try:
            try:
                store = self._store_factory(
                    self.data_root,
                    writer_token=self._writer_token,
                    writer_thread_id=threading.get_ident(),
                )
                store_status = store.status()
                resumed = ()
                if store_status.get("state") == "ready" and not store.read_only:
                    resumed = self._deletion_worker(store, self._writer_token).resume_pending()
                with self._lock:
                    self._store = store
                    if store_status.get("state") == "ready" and not store.read_only:
                        self._state = "ready"
                        self._reason_code = None
                        self._accepting = True
                        self._metrics["deletions_resumed"] += len(resumed)
                    else:
                        self._state = "degraded"
                        self._reason_code = str(
                            store_status.get("reason_code") or "store_unavailable"
                        )
                        self._accepting = False
            except Exception:
                with self._lock:
                    self._state = "degraded"
                    self._reason_code = "store_startup_failed"
                    self._last_error_reason = "store_startup_failed"
                    self._accepting = False
            finally:
                self._startup_complete.set()

            next_reconcile = time.monotonic() + self._reconcile_interval
            while True:
                if self._stop_requested.is_set() and self._queue.empty():
                    break
                command: _QueuedCommand | None = None
                try:
                    command = self._queue.get(timeout=0.05)
                except queue.Empty:
                    pass
                if command is not None:
                    try:
                        if command.future.set_running_or_notify_cancel():
                            if store is None:
                                raise MemoryServiceUnavailable(self._reason_code)
                            result = command.operation(store, self._writer_token)
                            command.future.set_result(result)
                            with self._lock:
                                self._metrics["commands_completed"] += 1
                    except BaseException as exc:
                        if not command.future.done():
                            command.future.set_exception(exc)
                        with self._lock:
                            self._metrics["commands_failed"] += 1
                            self._last_error_reason = _bounded_reason(exc)
                    finally:
                        self._queue.task_done()
                if (
                    store is not None
                    and self._conversation_store is not None
                    and self._state == "ready"
                    and time.monotonic() >= next_reconcile
                ):
                    try:
                        self._reconcile_terminal_page(store, self._writer_token)
                    except Exception as exc:
                        with self._lock:
                            self._metrics["reconcile_failures"] += 1
                            self._last_error_reason = _bounded_reason(exc)
                    next_reconcile = time.monotonic() + self._reconcile_interval
        finally:
            if store is not None:
                try:
                    store.close()
                except Exception:
                    with self._lock:
                        self._last_error_reason = "store_close_failed"
            with self._lock:
                self._accepting = False
                if self._state != "degraded" or self._stop_requested.is_set():
                    self._state = "stopped"

    def _reconcile_terminal_page(
        self, store: MemoryStore, writer_token: object
    ) -> dict[str, int]:
        conversation_store = self._conversation_store
        if conversation_store is None:
            return {"scanned": 0, "advanced": 0, "pending": 0}
        metadata = store.metadata()
        cursor = int(metadata.get("terminal_cursor", 0))
        page = conversation_store.scan_terminal_events(
            after_row_id=cursor,
            limit=self._reconcile_page_size,
        )
        source_store_id = str(page["source_store_id"])
        scanned = advanced = pending = projected = not_selected = 0
        for terminal in page.get("events", ()):
            scanned += 1
            outcome = str(terminal["outcome"])
            evidence = (
                conversation_store.read_request_evidence(
                    str(terminal["session_id"]), str(terminal["request_id"])
                )
                if outcome == "completed"
                else None
            )
            suppression = store.get_projection_suppression(
                source_store_id,
                str(terminal["session_id"]),
                str(terminal["request_id"]),
            )
            if evidence is not None and suppression is not None:
                evidence = dict(evidence)
                evidence["redacted"] = True
                evidence["redaction_receipt"] = {
                    "suppression_id": suppression["suppression_id"],
                    "reason_code": suppression["reason_code"],
                }
            existing = store.get_terminal_receipt(
                source_store_id,
                str(terminal["session_id"]),
                str(terminal["request_id"]),
            )
            decision = self._terminal_projector.decide(
                source_store_id=source_store_id,
                terminal=terminal,
                evidence=evidence,
                existing_receipt=existing,
            )
            if decision.candidate is not None:
                if not decision.write_receipt:
                    pending += 1
                    break
                store.record_terminal_receipt(
                    writer_token=writer_token,
                    **dict(decision.receipt_values),
                )
                extraction = self._episode_extractor.extract(decision.candidate, evidence)
                if extraction.episode is not None:
                    store.project_episode(
                        extraction.episode,
                        decision.receipt_values,
                        writer_token=writer_token,
                    )
                    projected += 1
                else:
                    final_values = dict(
                        decision.receipt_values,
                        projection_state="not_selected",
                        reason_code=extraction.selection.reason.value,
                        episode_id=None,
                        next_retry_at_utc=None,
                    )
                    store.complete_terminal_projection(
                        final_values,
                        writer_token=writer_token,
                    )
                    not_selected += 1
                advanced += 1
                continue
            if decision.advance_cursor:
                store.complete_terminal_projection(
                    decision.receipt_values,
                    writer_token=writer_token,
                )
                advanced += 1
                continue
            if decision.write_receipt:
                store.record_terminal_receipt(
                    writer_token=writer_token,
                    **dict(decision.receipt_values),
                )
            if decision.reason_code == "terminal_conflict":
                with self._lock:
                    self._state = "degraded"
                    self._reason_code = "terminal_conflict"
                    self._accepting = False
            if not decision.advance_cursor:
                pending += 1
                break
        with self._lock:
            self._metrics["terminal_scanned"] += scanned
            self._metrics["terminal_advanced"] += advanced
            self._metrics["terminal_pending"] += pending
            self._metrics["terminal_projected"] += projected
            self._metrics["terminal_not_selected"] += not_selected
        return {"scanned": scanned, "advanced": advanced, "pending": pending}


def _resolved_future(value: _T) -> Future[_T]:
    future: Future[_T] = Future()
    future.set_result(value)
    return future


def _bounded_reason(exc: BaseException) -> str:
    reason = getattr(exc, "reason_code", None)
    if type(reason) is str and reason:
        return reason[:96]
    return type(exc).__name__[:96]
