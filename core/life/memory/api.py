"""Capability-protected HTTP surface for governed autobiographical memory."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
from collections.abc import Callable, Mapping
from concurrent.futures import Future
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from core.runtime_access import RuntimeAccessDecision

from .access import (
    AccessBindingError,
    AccessContextFactory,
    PrincipalBindingSource,
    RESERVED_CLIENT_ACCESS_FIELDS,
    ServerPrincipal,
)
from .contracts import (
    AccessPurpose,
    ActorKind,
    ConfirmSharedMemory,
    CorrectMemory,
    DeletionScope,
    DeletionTargetSelector,
    ForgetMemory,
    MemoryItemKind,
    ProposeSharedMemory,
    RejectSharedMemory,
    RevokeSharedMemory,
    SourceHandling,
)
from .recall import build_recall_query
from .service import MemoryQueueFullError, MemoryService, MemoryServiceError
from .store import (
    MemoryStoreAuthorizationError,
    MemoryStoreConflictError,
    MemoryStoreError,
)


_FORBIDDEN_BODY_KEYS = RESERVED_CLIENT_ACCESS_FIELDS | {
    "acl",
    "access_token",
    "authorization",
    "capability",
    "token",
}
_CONTROL = frozenset(chr(value) for value in (*range(0, 32), 127))
_MAX_REQUEST_BODY_BYTES = 64 * 1024


def _api_error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def _bounded_text(value: Any, name: str, maximum: int, *, allow_empty: bool = False) -> str:
    if type(value) is not str:
        raise _api_error(400, "invalid_memory_request", f"{name} must be text")
    normalized = value.strip()
    if (
        (not normalized and not allow_empty)
        or len(normalized) > maximum
        or any(character in _CONTROL for character in normalized)
    ):
        raise _api_error(400, "invalid_memory_request", f"{name} is invalid")
    return normalized


def _bounded_int(value: Any, name: str, minimum: int = 1, maximum: int = 10_000) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise _api_error(400, "invalid_memory_request", f"{name} is invalid")
    return value


def _reject_authority_fields(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if type(key) is not str:
                raise _api_error(400, "invalid_memory_request", "request keys must be text")
            if key.casefold() in _FORBIDDEN_BODY_KEYS:
                raise _api_error(400, "reserved_access_field", f"{key} is server-owned")
            _reject_authority_fields(child)
    elif isinstance(value, list):
        for child in value:
            _reject_authority_fields(child)


def _strict_body(body: Any, allowed: set[str]) -> dict[str, Any]:
    if not isinstance(body, Mapping):
        raise _api_error(400, "invalid_memory_request", "request body must be an object")
    _reject_authority_fields(body)
    unknown = set(body) - allowed
    if unknown:
        raise _api_error(400, "invalid_memory_request", "request body has unsupported fields")
    return dict(body)


async def _read_json_body(request: Request) -> Any:
    declared = request.headers.get("content-length")
    if declared:
        try:
            if int(declared) > _MAX_REQUEST_BODY_BYTES:
                raise _api_error(413, "memory_request_too_large", "记忆请求内容过大")
        except ValueError as exc:
            raise _api_error(400, "invalid_memory_request", "Content-Length 无效") from exc
    raw = await request.body()
    if not raw:
        raise _api_error(400, "invalid_memory_request", "request body is required")
    if len(raw) > _MAX_REQUEST_BODY_BYTES:
        raise _api_error(413, "memory_request_too_large", "记忆请求内容过大")
    try:
        return json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _api_error(400, "invalid_memory_request", "request body must be valid JSON") from exc


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _command_id(operation: str, client_id_hash: str, idempotency_key: str) -> str:
    digest = hashlib.sha256(
        f"{operation}\0{client_id_hash}\0{idempotency_key}".encode("utf-8")
    ).hexdigest()
    return f"memory-command-{digest}"


async def _await_result(value: Any) -> Any:
    try:
        if inspect.isawaitable(value):
            return await value
        if isinstance(value, Future):
            return await asyncio.wrap_future(value)
        return value
    except MemoryStoreAuthorizationError as exc:
        raise _api_error(403, "memory_access_denied", "当前会话不能执行此记忆操作") from exc
    except MemoryStoreConflictError as exc:
        raise _api_error(409, "memory_revision_conflict", "记忆已发生变化，请刷新后重试") from exc
    except AccessBindingError as exc:
        raise _api_error(403, "memory_identity_binding_invalid", "当前会话身份不可用于记忆") from exc
    except MemoryStoreError as exc:
        raise _api_error(503, "memory_store_unavailable", "记忆存储暂时不可用") from exc
    except MemoryQueueFullError as exc:
        raise _api_error(503, exc.reason_code, "记忆服务正忙，请稍后重试") from exc
    except MemoryServiceError as exc:
        raise _api_error(503, exc.reason_code, "记忆服务暂时不可用") from exc
    except TimeoutError as exc:
        raise _api_error(503, "memory_timeout", "记忆操作等待超时") from exc


def _contract(contract_type, wire: Mapping[str, Any]):
    try:
        return contract_type.from_dict(dict(wire))
    except (TypeError, ValueError) as exc:
        raise _api_error(400, "invalid_memory_request", str(exc)[:240]) from exc


def _episode_view(item: Any) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "episode_id": item.episode_id,
        "revision": item.revision,
        "started_at_utc": item.started_at_utc,
        "ended_at_utc": item.ended_at_utc,
        "what_happened": item.what_happened,
        "intent_summary": item.intent_summary,
        "action_summary": item.action_summary,
        "verified_result_summary": item.verified_result_summary,
        "source_event_ids": list(item.source_event_ids),
        "source_message_ids": list(item.source_message_ids),
        "status": item.status.value,
    }


def _journal_view(item: Any) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "entry_id": item.entry_id,
        "revision": item.revision,
        "range_started_at_utc": item.range_started_at_utc,
        "range_ended_at_utc": item.range_ended_at_utc,
        "title": item.title,
        "body": item.body,
        "source_episode_ids": list(item.source_episode_ids),
        "status": item.status.value,
    }


def _shared_view(item: Any) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "shared_memory_id": item.shared_memory_id,
        "revision": item.revision,
        "proposal_revision": item.proposal_revision,
        "proposed_text": item.proposed_text,
        "status": item.status.value,
        "source_episode_ids": list(item.source_episode_ids),
        "confirmed_at_utc": item.confirmed_at_utc,
        "revoked_at_utc": item.revoked_at_utc,
        "updated_at_utc": item.updated_at_utc,
    }


def _recall_view(bundle: Any) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "query_id": bundle.query_id,
        "items": [
            {
                "item_id": item.item_id,
                "item_kind": item.item_kind.value,
                "prompt_text": item.prompt_text,
                "occurred_at_utc": item.occurred_at_utc,
                "epistemic_label": item.epistemic_label.value,
                "source_citation_token": item.source_citation_token,
                "owner_label": item.owner_label.value,
                "audience": item.audience.value,
                "confidence": item.confidence,
            }
            for item in bundle.items
        ],
        "generated_at_utc": bundle.generated_at_utc,
        "truncated": bundle.truncated,
        "reason_code": bundle.reason_code,
    }


def create_memory_router(
    memory_service: MemoryService,
    access_factory: AccessContextFactory,
    authorize: Callable[[Request, str], RuntimeAccessDecision],
    *,
    legacy_root: str | Path | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/life/memory", tags=["life-memory"])
    archive_root = None if legacy_root is None else Path(legacy_root).resolve()

    async def context_for(request: Request, scope: str, purpose: AccessPurpose):
        decision = authorize(request, scope)
        if not isinstance(decision, RuntimeAccessDecision) or not decision.allowed:
            reason = getattr(decision, "reason_code", "missing_capability")
            close_code = getattr(decision, "close_code", 4401)
            status = 401 if close_code == 4401 else 403
            raise _api_error(status, str(reason), "记忆访问授权无效")
        authorized = decision.principal
        if authorized is None:
            raise _api_error(401, "missing_principal", "记忆访问授权无效")
        session_id = _bounded_text(
            request.headers.get("x-javis-session-id", ""),
            "session id",
            256,
        )
        try:
            principal = ServerPrincipal(
                runtime_boot_id=authorized.runtime_boot_id,
                session_id=session_id,
                client_id_hash=authorized.client_id_hash,
                capability_scopes=authorized.scopes,
                issued_at_epoch=authorized.issued_at_epoch,
                expires_at_epoch=authorized.expires_at_epoch,
                binding_source=PrincipalBindingSource(authorized.binding_source),
            )
            context = access_factory.for_session(
                session_id,
                principal=principal,
                purpose=purpose,
            )
        except (TypeError, ValueError, RuntimeError) as exc:
            raise _api_error(403, "memory_identity_binding_invalid", "当前会话身份不可用于记忆") from exc
        if context.actor_kind is ActorKind.GUEST:
            raise _api_error(403, "memory_guest_denied", "访客会话不能读取或修改私人记忆")
        return context

    async def list_page(request: Request, kind: MemoryItemKind, limit: int, cursor: str | None):
        context = await context_for(request, "memory.read", AccessPurpose.RECALL)
        if cursor is not None:
            cursor = _bounded_text(cursor, "cursor", 256)
        page, next_cursor = await _await_result(
            memory_service.list_items(
                context,
                (kind,),
                statuses=("active",),
                cursor=cursor,
                limit=limit,
            )
        )
        projector = _episode_view if kind is MemoryItemKind.EXPERIENCE_EPISODE else _journal_view
        return {
            "schema_version": 1,
            "items": [projector(item) for item in page],
            "next_cursor": next_cursor,
        }

    @router.get("/recall")
    async def recall(
        request: Request,
        q: str = Query(min_length=1, max_length=512),
        limit: int = Query(default=8, ge=1, le=20),
    ):
        context = await context_for(request, "memory.read", AccessPurpose.RECALL)
        bundle = await _await_result(
            memory_service.recall(build_recall_query(context, q, limit=limit))
        )
        return _recall_view(bundle)

    @router.get("/episodes")
    async def episodes(
        request: Request,
        limit: int = Query(default=100, ge=1, le=200),
        cursor: str | None = Query(default=None, max_length=256),
    ):
        return await list_page(request, MemoryItemKind.EXPERIENCE_EPISODE, limit, cursor)

    @router.get("/journal")
    async def journal(
        request: Request,
        limit: int = Query(default=100, ge=1, le=200),
        cursor: str | None = Query(default=None, max_length=256),
    ):
        return await list_page(request, MemoryItemKind.JOURNAL_ENTRY, limit, cursor)

    @router.get("/status")
    async def status(request: Request):
        context = await context_for(request, "memory.read", AccessPurpose.RECALL)
        shared, _ = await _await_result(
            memory_service.list_items(
                context,
                (MemoryItemKind.SHARED_MEMORY,),
                statuses=("active", "candidate", "superseded"),
                limit=200,
            )
        )
        migration = await _await_result(memory_service.legacy_migration_summary())
        service_state = str(memory_service.status().get("state") or "degraded")
        visible_state = "ready" if service_state == "ready" else "degraded"
        quarantined = int(migration.get("quarantined_count", 0))
        candidates = int(migration.get("candidate_count", 0))
        archive_exists = bool(archive_root is not None and archive_root.is_dir())
        if visible_state != "ready":
            legacy_state = "degraded"
        elif quarantined:
            legacy_state = "quarantined"
        elif candidates:
            legacy_state = "migration_pending"
        elif archive_exists:
            legacy_state = "read_only"
        else:
            legacy_state = "absent"
        return {
            "schema_version": 1,
            "service_state": visible_state,
            "legacy": {
                "state": legacy_state,
                "quarantine_count": quarantined,
                "candidate_count": candidates,
                "last_scan_at_utc": migration.get("last_scan_at_utc"),
                "detail": "旧记忆只读归档不会自动进入召回",
            },
            "shared_memories": [_shared_view(item) for item in shared],
        }

    @router.post("/shared/proposals")
    async def propose_shared(request: Request):
        context = await context_for(request, "memory.manage", AccessPurpose.MANAGE)
        body = await _read_json_body(request)
        data = _strict_body(body, {"source_episode_ids", "proposed_text", "idempotency_key"})
        source_ids = data.get("source_episode_ids")
        if not isinstance(source_ids, list) or not 1 <= len(source_ids) <= 64:
            raise _api_error(400, "invalid_memory_request", "source_episode_ids is invalid")
        source_ids = [_bounded_text(value, "source episode id", 256) for value in source_ids]
        idempotency = _bounded_text(data.get("idempotency_key"), "idempotency key", 256)
        issued = _utc_now()
        command_id = _command_id("shared.propose", context.client_id_hash, idempotency)
        command = _contract(
            ProposeSharedMemory,
            {
                "schema_version": 1,
                "command_id": command_id,
                "access_context": context.to_dict(),
                "source_episode_ids": source_ids,
                "proposed_text": _bounded_text(data.get("proposed_text"), "proposed text", 8_000),
                "idempotency_key": idempotency,
                "issued_at_utc": issued,
            },
        )
        result = await _await_result(memory_service.propose_shared_memory(command))
        return {"schema_version": 1, "command_id": command_id, "status": "completed", "resource_id": result.shared_memory_id}

    async def shared_transition(request: Request, shared_id: str, operation: str):
        context = await context_for(request, "memory.manage", AccessPurpose.MANAGE)
        body = await _read_json_body(request)
        shared_id = _bounded_text(shared_id, "shared memory id", 256)
        revision_key = "expected_revision" if operation == "revoke" else "proposal_revision"
        data = _strict_body(body, {revision_key, "idempotency_key"})
        revision = _bounded_int(data.get(revision_key), revision_key)
        idempotency = _bounded_text(data.get("idempotency_key"), "idempotency key", 256)
        command_id = _command_id(f"shared.{operation}", context.client_id_hash, idempotency)
        wire = {
            "schema_version": 1,
            "command_id": command_id,
            "access_context": context.to_dict(),
            "shared_memory_id": shared_id,
            revision_key: revision,
            "idempotency_key": idempotency,
            "issued_at_utc": _utc_now(),
        }
        contract_type = {
            "confirm": ConfirmSharedMemory,
            "reject": RejectSharedMemory,
            "revoke": RevokeSharedMemory,
        }[operation]
        command = _contract(contract_type, wire)
        method = getattr(memory_service, f"{operation}_shared_memory")
        result = await _await_result(method(command))
        return {"schema_version": 1, "command_id": command_id, "status": "completed", "resource_id": result.shared_memory_id}

    @router.post("/shared/{shared_id}/confirm")
    async def confirm_shared(request: Request, shared_id: str):
        return await shared_transition(request, shared_id, "confirm")

    @router.post("/shared/{shared_id}/reject")
    async def reject_shared(request: Request, shared_id: str):
        return await shared_transition(request, shared_id, "reject")

    @router.post("/shared/{shared_id}/revoke")
    async def revoke_shared(request: Request, shared_id: str):
        return await shared_transition(request, shared_id, "revoke")

    @router.post("/corrections")
    async def correct(request: Request):
        context = await context_for(request, "memory.manage", AccessPurpose.MANAGE)
        body = await _read_json_body(request)
        data = _strict_body(
            body,
            {"target_kind", "target_id", "expected_revision", "corrected_text", "source_evidence_ids", "idempotency_key"},
        )
        evidence = data.get("source_evidence_ids")
        if not isinstance(evidence, list) or not 1 <= len(evidence) <= 64:
            raise _api_error(400, "invalid_memory_request", "source_evidence_ids is invalid")
        idempotency = _bounded_text(data.get("idempotency_key"), "idempotency key", 256)
        command_id = _command_id("memory.correct", context.client_id_hash, idempotency)
        command = _contract(
            CorrectMemory,
            {
                "schema_version": 1,
                "command_id": command_id,
                "access_context": context.to_dict(),
                "target_kind": data.get("target_kind"),
                "target_id": _bounded_text(data.get("target_id"), "target id", 256),
                "expected_revision": _bounded_int(data.get("expected_revision"), "expected revision"),
                "corrected_text": _bounded_text(data.get("corrected_text"), "corrected text", 32_768),
                "source_evidence_ids": [_bounded_text(value, "source evidence id", 256) for value in evidence],
                "idempotency_key": idempotency,
                "issued_at_utc": _utc_now(),
            },
        )
        result = await _await_result(memory_service.correct_memory(command))
        resource_id = getattr(result, "episode_id", None) or getattr(result, "entry_id", None)
        return {"schema_version": 1, "command_id": command_id, "status": "completed", "resource_id": resource_id}

    @router.post("/deletions")
    async def forget(request: Request):
        context = await context_for(request, "memory.delete", AccessPurpose.DELETE)
        body = await _read_json_body(request)
        data = _strict_body(
            body,
            {
                "scope", "target_kind", "target_id", "session_id", "range_started_at_utc",
                "range_ended_at_utc", "source_handling", "reason_code", "idempotency_key",
            },
        )
        try:
            scope = DeletionScope(data.get("scope"))
            source_handling = SourceHandling(data.get("source_handling"))
        except (TypeError, ValueError) as exc:
            raise _api_error(400, "invalid_memory_request", "deletion scope is invalid") from exc
        selector = {
            "item_kind": None,
            "item_id": None,
            "subject_id": None,
            "session_id": None,
            "range_started_at_utc": None,
            "range_ended_at_utc": None,
        }
        if scope is DeletionScope.ITEM:
            selector["item_kind"] = data.get("target_kind")
            selector["item_id"] = _bounded_text(data.get("target_id"), "target id", 256)
        elif scope in {DeletionScope.EPISODE, DeletionScope.SHARED_MEMORY}:
            selector["item_id"] = _bounded_text(data.get("target_id"), "target id", 256)
        elif scope is DeletionScope.SUBJECT_OWNED:
            selector["subject_id"] = context.actor_subject_id
        elif scope is DeletionScope.SESSION_DERIVED:
            requested_session = data.get("session_id", context.session_id)
            if requested_session != context.session_id:
                raise _api_error(403, "memory_session_scope_denied", "只能遗忘当前会话形成的记忆")
            selector["session_id"] = context.session_id
        elif scope is DeletionScope.TIME_RANGE:
            selector["range_started_at_utc"] = data.get("range_started_at_utc")
            selector["range_ended_at_utc"] = data.get("range_ended_at_utc")
        target_selector = _contract(DeletionTargetSelector, selector)
        idempotency = _bounded_text(data.get("idempotency_key"), "idempotency key", 256)
        deletion_id = "deletion-" + hashlib.sha256(
            f"{context.actor_subject_id}\0{idempotency}".encode("utf-8")
        ).hexdigest()
        command_id = _command_id("memory.delete", context.client_id_hash, idempotency)
        command = _contract(
            ForgetMemory,
            {
                "schema_version": 1,
                "command_id": command_id,
                "access_context": context.to_dict(),
                "deletion_request_id": deletion_id,
                "scope": scope.value,
                "target_selector": target_selector.to_dict(),
                "source_handling": source_handling.value,
                "reason_code": _bounded_text(data.get("reason_code"), "reason code", 96),
                "idempotency_key": idempotency,
                "issued_at_utc": _utc_now(),
            },
        )
        await _await_result(memory_service.forget_memory(command))
        return {"schema_version": 1, "command_id": command_id, "status": "completed", "resource_id": deletion_id}

    @router.get("/deletions/{deletion_id}")
    async def deletion_status(request: Request, deletion_id: str):
        context = await context_for(request, "memory.delete", AccessPurpose.DELETE)
        deletion_id = _bounded_text(deletion_id, "deletion id", 256)
        result = await _await_result(memory_service.deletion_status(deletion_id, context))
        if result is None:
            raise _api_error(404, "memory_deletion_not_found", "未找到此遗忘请求")
        return {"schema_version": 1, **result}

    return router


__all__ = ["create_memory_router"]
