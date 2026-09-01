"""Recoverable deletion orchestration for autobiographical memory."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Protocol

from .contracts import DeletionState, ForgetMemory, SourceHandling


class ConversationDeletionStore(Protocol):
    def redact_request_evidence(
        self,
        session_id: str,
        request_id: str,
        deletion_id: str,
    ) -> Mapping[str, Any]: ...


class DeletionWorker:
    """Advance one deletion through durable, independently retryable stages."""

    def __init__(
        self,
        store: Any,
        writer_token: object,
        *,
        conversation_store: ConversationDeletionStore | None,
        cache_invalidator: Callable[[], None],
        prompt_invalidators: tuple[Callable[[], None], ...] = (),
        after_stage: Callable[[str, str], None] | None = None,
    ) -> None:
        self._store = store
        self._writer_token = writer_token
        self._conversation_store = conversation_store
        self._cache_invalidator = cache_invalidator
        self._prompt_invalidators = prompt_invalidators
        self._after_stage = after_stage

    def execute(self, command: ForgetMemory) -> dict[str, Any]:
        if not isinstance(command, ForgetMemory):
            raise TypeError("command must be ForgetMemory")
        status = self._store.prepare_deletion(command, writer_token=self._writer_token)
        self._notify(str(status["state"]), str(status["deletion_request_id"]))
        return self._continue(str(status["deletion_request_id"]))

    def resume_pending(self) -> tuple[dict[str, Any], ...]:
        results: list[dict[str, Any]] = []
        for deletion_id in self._store.pending_deletion_ids():
            try:
                results.append(self._continue(deletion_id))
            except Exception:
                status = self._store.deletion_status(deletion_id)
                if status is not None:
                    results.append(status)
        return tuple(results)

    def _continue(self, deletion_id: str) -> dict[str, Any]:
        while True:
            status = self._store.deletion_status(deletion_id)
            if status is None:
                raise RuntimeError("deletion_request_missing")
            state = DeletionState(str(status["state"]))
            if state is DeletionState.VERIFIED:
                return status
            try:
                if state is DeletionState.ACCEPTED:
                    status = self._store.fence_deletion(
                        deletion_id, writer_token=self._writer_token
                    )
                elif state is DeletionState.FENCED:
                    status = self._store.advance_deletion_state(
                        deletion_id,
                        expected=DeletionState.FENCED,
                        target=DeletionState.SOURCE_PENDING,
                        writer_token=self._writer_token,
                    )
                elif state is DeletionState.SOURCE_PENDING:
                    self._process_sources(status)
                    status = self._store.advance_deletion_state(
                        deletion_id,
                        expected=DeletionState.SOURCE_PENDING,
                        target=DeletionState.SOURCE_RETAINED,
                        writer_token=self._writer_token,
                    )
                elif state is DeletionState.SOURCE_RETAINED:
                    status = self._store.delete_deletion_primary_rows(
                        deletion_id, writer_token=self._writer_token
                    )
                elif state is DeletionState.PRIMARY_ROWS_DELETED:
                    status = self._store.delete_deletion_derivations(
                        deletion_id, writer_token=self._writer_token
                    )
                elif state is DeletionState.DERIVATIONS_DELETED:
                    status = self._store.delete_deletion_fts(
                        deletion_id, writer_token=self._writer_token
                    )
                elif state is DeletionState.FTS_DELETED:
                    self._cache_invalidator()
                    status = self._store.advance_deletion_state(
                        deletion_id,
                        expected=DeletionState.FTS_DELETED,
                        target=DeletionState.CACHES_INVALIDATED,
                        writer_token=self._writer_token,
                    )
                elif state is DeletionState.CACHES_INVALIDATED:
                    for invalidate in self._prompt_invalidators:
                        invalidate()
                    status = self._store.advance_deletion_state(
                        deletion_id,
                        expected=DeletionState.CACHES_INVALIDATED,
                        target=DeletionState.PROMPT_INVALIDATED,
                        writer_token=self._writer_token,
                    )
                elif state is DeletionState.PROMPT_INVALIDATED:
                    status = self._store.verify_deletion(
                        deletion_id, writer_token=self._writer_token
                    )
                else:
                    raise RuntimeError("deletion_state_invalid")
            except Exception as exc:
                self._store.record_deletion_failure(
                    deletion_id,
                    type(exc).__name__[:96],
                    writer_token=self._writer_token,
                )
                raise
            self._notify(str(status["state"]), deletion_id)

    def _process_sources(self, status: Mapping[str, Any]) -> None:
        if str(status["source_handling"]) != SourceHandling.SOURCE_AND_DERIVED.value:
            return
        if self._conversation_store is None:
            raise RuntimeError("conversation_source_unavailable")
        deletion_id = str(status["deletion_request_id"])
        for source in self._store.deletion_sources(deletion_id):
            receipt = self._conversation_store.redact_request_evidence(
                str(source["session_id"]),
                str(source["request_id"]),
                deletion_id,
            )
            if (
                str(receipt.get("session_id") or "") != str(source["session_id"])
                or str(receipt.get("request_id") or "") != str(source["request_id"])
                or str(receipt.get("deletion_id") or "") != deletion_id
            ):
                raise RuntimeError("conversation_redaction_receipt_invalid")

    def _notify(self, state: str, deletion_id: str) -> None:
        if self._after_stage is not None:
            self._after_stage(state, deletion_id)


__all__ = ["ConversationDeletionStore", "DeletionWorker"]
