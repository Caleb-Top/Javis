from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from core.conversation_store import ConversationStore
from core.life.memory.access import (
    AccessContextFactory,
    MemoryServiceAccessView,
    PrincipalBindingSource,
    ServerPrincipal,
)
from core.life.memory.api import create_memory_router
from core.life.memory.contracts import (
    AccessPurpose,
    ExperienceEpisode,
    JournalEntry,
)
from core.life.memory.service import MemoryService
from core.life.memory.subjects import BootstrapPrimary
from core.runtime_access import RuntimeAccessAuthority, create_http_authorizer


BOOT_ID = "boot-memory-api"
CLIENT_ID = "desktop-main"
SESSION_ID = "session-memory-api"
TAURI_ORIGIN = "http://tauri.localhost"
NOW = "2026-08-20T10:00:00.000Z"
ENDED = "2026-08-20T10:05:00.000Z"
HASH_A = "a" * 64
ALL_SCOPES = (
    "conversation",
    "identity.manage",
    "memory.delete",
    "memory.manage",
    "memory.migrate",
    "memory.read",
)


@dataclass
class MemoryApiHarness:
    app: FastAPI
    authority: RuntimeAccessAuthority
    service: MemoryService
    conversations: ConversationStore
    token: str
    actor_subject_id: str
    participant_subject_ids: tuple[str, ...]


def _server_principal(
    authority: RuntimeAccessAuthority,
    token: str,
    *,
    scope: str,
    session_id: str = SESSION_ID,
) -> ServerPrincipal:
    decision = authority.validate(
        token,
        scope=scope,
        origin=TAURI_ORIGIN,
        peer_host="127.0.0.1",
    )
    assert decision.allowed and decision.principal is not None
    principal = decision.principal
    return ServerPrincipal(
        runtime_boot_id=principal.runtime_boot_id,
        session_id=session_id,
        client_id_hash=principal.client_id_hash,
        capability_scopes=principal.scopes,
        issued_at_epoch=principal.issued_at_epoch,
        expires_at_epoch=principal.expires_at_epoch,
        binding_source=PrincipalBindingSource(principal.binding_source),
    )


def _record_conversation_request(
    store: ConversationStore,
    request_id: str,
    text: str,
) -> None:
    store.accept_request(
        SESSION_ID,
        request_id,
        f"request-key-{request_id}",
        text,
        {
            "access_projection": {
                "schema_version": 1,
                "context_id": f"accepted-{request_id}",
                "actor_kind": "primary_user",
                "session_id": SESSION_ID,
                "audience_ceiling": "owner_private",
                "identity_assurance": "desktop_confirmed",
                "acl_epoch": 0,
            },
            "execution_lane": "exclusive",
        },
    )
    store.append_message(
        SESSION_ID,
        request_id,
        "assistant",
        "The governed request completed.",
    )
    store.append_event(SESSION_ID, request_id, "request.completed", {})


def _episode(
    episode_id: str,
    request_id: str,
    text: str,
    *,
    owner_subject_id: str,
    participant_subject_ids: tuple[str, ...],
) -> ExperienceEpisode:
    return ExperienceEpisode.from_dict(
        {
            "schema_version": 1,
            "episode_id": episode_id,
            "revision": 1,
            "owner_subject_id": owner_subject_id,
            "audience": "owner_private",
            "privacy_class": "user_private",
            "session_id": SESSION_ID,
            "request_id": request_id,
            "participant_subject_ids": list(participant_subject_ids),
            "started_at_utc": NOW,
            "ended_at_utc": ENDED,
            "outcome": "completed",
            "what_happened": text,
            "javis_attention": "The user explicitly asked Javis to preserve this evidence.",
            "intent_summary": "Exercise the governed memory HTTP surface.",
            "action_summary": "Javis retained only evidence-backed memory.",
            "verified_result_summary": "The terminal evidence was complete.",
            "meaning_for_user": "",
            "meaning_for_javis": "",
            "source_terminal_event_id": f"terminal-{request_id}",
            "source_terminal_sequence": 3,
            "source_sequence_domain": f"conversation-store:{SESSION_ID}",
            "source_message_ids": [f"message-user-{request_id}", f"message-javis-{request_id}"],
            "source_event_ids": [f"accepted-{request_id}", f"terminal-{request_id}"],
            "source_digest": hashlib.sha256(request_id.encode("utf-8")).hexdigest(),
            "extractor_version": "deterministic.v1",
            "confidence": 0.95,
            "status": "active",
            "retention_class": "memory_candidate",
            "expires_at_utc": None,
            "created_at_utc": NOW,
            "updated_at_utc": ENDED,
        }
    )


def _journal(
    owner_subject_id: str,
    source_episode_id: str,
) -> JournalEntry:
    return JournalEntry.from_dict(
        {
            "schema_version": 1,
            "entry_id": "journal-api-1",
            "revision": 1,
            "owner_subject_id": owner_subject_id,
            "audience": "owner_private",
            "privacy_class": "user_private",
            "range_started_at_utc": NOW,
            "range_ended_at_utc": ENDED,
            "title": "Memory API continuity",
            "body": "The memory API continuity marker remained evidence-backed.",
            "source_episode_ids": [source_episode_id],
            "entry_kind": "boundary_reflection",
            "source_digest": HASH_A,
            "status": "active",
            "retention_class": "continuity",
            "expires_at_utc": None,
            "created_at_utc": NOW,
            "updated_at_utc": ENDED,
        }
    )


@pytest.fixture
def memory_api(tmp_path: Path):
    conversations = ConversationStore(tmp_path / "conversations.sqlite3")
    for request_id, text in (
        ("request-base", "Preserve the memory API continuity marker."),
        ("request-correction", "The original governed date was Tuesday."),
        ("request-derived-delete", "Forget only this derived memory."),
        ("request-source-delete", "Forget this memory and its source evidence."),
    ):
        _record_conversation_request(conversations, request_id, text)

    service = MemoryService(
        tmp_path / "data",
        conversation_store=conversations,
        reconcile_interval_seconds=60,
        runtime_boot_id=BOOT_ID,
        javis_identity_id="identity-memory-api",
    ).start()
    authority = RuntimeAccessAuthority(BOOT_ID)
    issued = authority.issue(CLIENT_ID, ALL_SCOPES)
    access_factory = AccessContextFactory(
        MemoryServiceAccessView(service, timeout=5),
        runtime_boot_id=BOOT_ID,
    )
    binding_principal = _server_principal(
        authority, issued.token, scope="identity.manage"
    )
    bootstrap_context = access_factory.for_identity_management(
        SESSION_ID,
        principal=binding_principal,
    )
    bootstrap = BootstrapPrimary.from_dict(
        {
            "schema_version": 1,
            "command_id": "command-memory-api-bootstrap",
            "access_context": bootstrap_context.to_dict(),
            "display_name": "Primary user",
            "aliases": [],
            "explicit_confirmation": True,
            "idempotency_key": "memory-api-bootstrap",
            "issued_at_utc": bootstrap_context.issued_at_utc,
        }
    )
    service.bootstrap_primary(bootstrap, binding_principal).result(timeout=10)
    manage_context = access_factory.for_session(
        SESSION_ID,
        principal=_server_principal(authority, issued.token, scope="memory.manage"),
        purpose=AccessPurpose.MANAGE,
    )
    assert manage_context.actor_kind.value == "primary_user"
    service.set_source_progress(conversations.source_store_id(), 0).result(timeout=10)

    episodes = (
        _episode(
            "episode-api-base",
            "request-base",
            "The memory API continuity marker was verified.",
            owner_subject_id=manage_context.actor_subject_id,
            participant_subject_ids=manage_context.participant_subject_ids,
        ),
        _episode(
            "episode-api-correction",
            "request-correction",
            "The original governed date was Tuesday.",
            owner_subject_id=manage_context.actor_subject_id,
            participant_subject_ids=manage_context.participant_subject_ids,
        ),
        _episode(
            "episode-api-derived-delete",
            "request-derived-delete",
            "Delete this derived-only marker.",
            owner_subject_id=manage_context.actor_subject_id,
            participant_subject_ids=manage_context.participant_subject_ids,
        ),
        _episode(
            "episode-api-source-delete",
            "request-source-delete",
            "Delete this source-and-derived marker.",
            owner_subject_id=manage_context.actor_subject_id,
            participant_subject_ids=manage_context.participant_subject_ids,
        ),
    )
    for episode in episodes:
        service.put_item(episode).result(timeout=10)
    service.put_item(_journal(manage_context.actor_subject_id, episodes[0].episode_id)).result(
        timeout=10
    )

    legacy_root = tmp_path / "brain_data"
    legacy_root.mkdir()
    app = FastAPI()
    app.include_router(
        create_memory_router(
            service,
            access_factory,
            create_http_authorizer(authority),
            legacy_root=legacy_root,
        )
    )
    harness = MemoryApiHarness(
        app=app,
        authority=authority,
        service=service,
        conversations=conversations,
        token=issued.token,
        actor_subject_id=manage_context.actor_subject_id,
        participant_subject_ids=manage_context.participant_subject_ids,
    )
    try:
        yield harness
    finally:
        assert service.shutdown(timeout=15)


def _request(
    harness: MemoryApiHarness,
    method: str,
    path: str,
    *,
    token: str | None = None,
    session_id: str = SESSION_ID,
    body: dict[str, Any] | None = None,
) -> httpx.Response:
    async def send() -> httpx.Response:
        headers = {
            "origin": TAURI_ORIGIN,
            "x-javis-session-id": session_id,
        }
        selected_token = harness.token if token is None else token
        if selected_token:
            headers["x-javis-runtime-capability"] = selected_token
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=harness.app),
            base_url="http://test",
        ) as client:
            return await client.request(method, path, headers=headers, json=body)

    return asyncio.run(send())


def _response_code(response: httpx.Response) -> str:
    detail = response.json().get("detail", {})
    return str(detail.get("code") if isinstance(detail, dict) else detail)


def _all_keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {key for child in value.values() for key in _all_keys(child)}
    if isinstance(value, list):
        return {key for child in value for key in _all_keys(child)}
    return set()


def _assert_no_authority_leak(harness: MemoryApiHarness, payload: Any) -> None:
    forbidden = {
        "access_context",
        "access_token",
        "acl",
        "acl_epoch",
        "actor_subject_id",
        "audience_subject_ids",
        "authorization",
        "capability",
        "capability_scopes",
        "client_id_hash",
        "credential_reference_hash",
        "owner_subject_id",
        "participant_subject_ids",
        "runtime_boot_id",
        "subject_id",
        "token",
    }
    assert _all_keys(payload).isdisjoint(forbidden)
    rendered = repr(payload)
    assert harness.token not in rendered
    assert harness.actor_subject_id not in rendered
    assert "subject-primary-" not in rendered


def test_router_exposes_exactly_the_eleven_governed_memory_operations(memory_api):
    paths = memory_api.app.openapi()["paths"]
    actual = {
        (method.upper(), path)
        for path, operations in paths.items()
        if path.startswith("/api/life/memory")
        for method in operations
        if method.upper() in {"GET", "POST"}
    }
    assert actual == {
        ("GET", "/api/life/memory/recall"),
        ("GET", "/api/life/memory/episodes"),
        ("GET", "/api/life/memory/journal"),
        ("GET", "/api/life/memory/status"),
        ("POST", "/api/life/memory/shared/proposals"),
        ("POST", "/api/life/memory/shared/{shared_id}/confirm"),
        ("POST", "/api/life/memory/shared/{shared_id}/reject"),
        ("POST", "/api/life/memory/shared/{shared_id}/revoke"),
        ("POST", "/api/life/memory/corrections"),
        ("POST", "/api/life/memory/deletions"),
        ("GET", "/api/life/memory/deletions/{deletion_id}"),
    }


def test_capability_scope_guest_and_server_owned_body_fields_fail_closed(memory_api):
    missing = _request(
        memory_api,
        "POST",
        "/api/life/memory/shared/proposals",
        token="",
        body={"owner_subject_id": "subject-forged"},
    )
    assert missing.status_code == 401
    assert _response_code(missing) == "missing_capability"

    wrong_scope = memory_api.authority.issue(CLIENT_ID, ("life.read",))
    denied = _request(
        memory_api,
        "GET",
        "/api/life/memory/status",
        token=wrong_scope.token,
    )
    assert denied.status_code == 403
    assert _response_code(denied) == "scope_denied"

    read_only = memory_api.authority.issue(CLIENT_ID, ("conversation", "memory.read"))
    manage_denied = _request(
        memory_api,
        "POST",
        "/api/life/memory/shared/proposals",
        token=read_only.token,
        body={
            "source_episode_ids": ["episode-api-base"],
            "proposed_text": "Must not pass a read-only capability.",
            "idempotency_key": "read-only-manage-denied",
        },
    )
    assert manage_denied.status_code == 403
    assert _response_code(manage_denied) == "scope_denied"

    guest = memory_api.authority.issue("desktop-unbound", ("memory.read",))
    guest_response = _request(
        memory_api,
        "GET",
        "/api/life/memory/status",
        token=guest.token,
    )
    assert guest_response.status_code == 403
    assert _response_code(guest_response) == "memory_guest_denied"

    valid_proposal = {
        "source_episode_ids": ["episode-api-base"],
        "proposed_text": "A governed proposal",
        "idempotency_key": "authority-injection-proposal",
    }
    for forged in (
        {**valid_proposal, "owner_subject_id": "subject-forged"},
        {**valid_proposal, "audience": "explicit_shared"},
        {
            **valid_proposal,
            "source_episode_ids": [
                {"participant_subject_ids": ["subject-forged"]}
            ],
        },
    ):
        response = _request(
            memory_api,
            "POST",
            "/api/life/memory/shared/proposals",
            body=forged,
        )
        assert response.status_code == 400
        assert _response_code(response) == "reserved_access_field"

    async def malformed_without_capability() -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=memory_api.app),
            base_url="http://test",
        ) as client:
            return await client.post(
                "/api/life/memory/shared/proposals",
                headers={
                    "origin": TAURI_ORIGIN,
                    "x-javis-session-id": SESSION_ID,
                    "content-type": "application/json",
                },
                content=b"{not-valid-json",
            )

    auth_first = asyncio.run(malformed_without_capability())
    assert auth_first.status_code == 401
    assert _response_code(auth_first) == "missing_capability"


def test_explicitly_bound_client_can_open_a_new_session_without_auto_participants(memory_api):
    new_session = "session-memory-first-open"
    response = _request(
        memory_api,
        "GET",
        "/api/life/memory/status",
        session_id=new_session,
    )

    assert response.status_code == 200, response.text
    participants = memory_api.service.active_session_participants(new_session).result(timeout=10)
    assert participants == ()
    _assert_no_authority_leak(memory_api, response.json())


def test_read_routes_and_legacy_status_return_only_safe_ui_projections(memory_api):
    responses = (
        _request(memory_api, "GET", "/api/life/memory/recall?q=continuity%20marker"),
        _request(memory_api, "GET", "/api/life/memory/episodes?limit=20"),
        _request(memory_api, "GET", "/api/life/memory/journal?limit=20"),
        _request(memory_api, "GET", "/api/life/memory/status"),
    )
    for response in responses:
        assert response.status_code == 200, response.text
        _assert_no_authority_leak(memory_api, response.json())

    recall, episodes, journal, status = (response.json() for response in responses)
    assert any("continuity marker" in item["prompt_text"] for item in recall["items"])
    assert {item["episode_id"] for item in episodes["items"]} >= {
        "episode-api-base",
        "episode-api-correction",
    }
    assert [item["entry_id"] for item in journal["items"]] == ["journal-api-1"]
    assert status["service_state"] == "ready"
    assert status["legacy"] == {
        "state": "read_only",
        "quarantine_count": 0,
        "candidate_count": 0,
        "last_scan_at_utc": None,
        "detail": status["legacy"]["detail"],
    }


def _proposal(memory_api: MemoryApiHarness, suffix: str) -> httpx.Response:
    return _request(
        memory_api,
        "POST",
        "/api/life/memory/shared/proposals",
        body={
            "source_episode_ids": ["episode-api-base"],
            "proposed_text": f"Governed shared proposal {suffix}",
            "idempotency_key": f"proposal-{suffix}",
        },
    )


def test_shared_proposal_double_click_stale_revision_confirm_reject_and_revoke(memory_api):
    proposed = _proposal(memory_api, "confirm-and-revoke")
    replay = _proposal(memory_api, "confirm-and-revoke")
    assert proposed.status_code == replay.status_code == 200
    assert replay.json() == proposed.json()
    shared_id = proposed.json()["resource_id"]

    stale = _request(
        memory_api,
        "POST",
        f"/api/life/memory/shared/{shared_id}/confirm",
        body={"proposal_revision": 2, "idempotency_key": "confirm-stale"},
    )
    assert stale.status_code == 409
    assert _response_code(stale) == "memory_revision_conflict"

    confirmed = _request(
        memory_api,
        "POST",
        f"/api/life/memory/shared/{shared_id}/confirm",
        body={"proposal_revision": 1, "idempotency_key": "confirm-valid"},
    )
    assert confirmed.status_code == 200, confirmed.text
    status = _request(memory_api, "GET", "/api/life/memory/status")
    current = next(
        item for item in status.json()["shared_memories"]
        if item["shared_memory_id"] == shared_id
    )
    assert current["status"] == "confirmed"

    revoked = _request(
        memory_api,
        "POST",
        f"/api/life/memory/shared/{shared_id}/revoke",
        body={
            "expected_revision": current["revision"],
            "idempotency_key": "revoke-valid",
        },
    )
    assert revoked.status_code == 200, revoked.text

    rejected_proposal = _proposal(memory_api, "reject")
    assert rejected_proposal.status_code == 200
    rejected_id = rejected_proposal.json()["resource_id"]
    rejected = _request(
        memory_api,
        "POST",
        f"/api/life/memory/shared/{rejected_id}/reject",
        body={"proposal_revision": 1, "idempotency_key": "reject-valid"},
    )
    assert rejected.status_code == 200, rejected.text

    for response in (proposed, replay, stale, confirmed, status, revoked, rejected_proposal, rejected):
        _assert_no_authority_leak(memory_api, response.json())


def test_correction_and_both_deletion_modes_report_verified_progress(memory_api):
    correction = _request(
        memory_api,
        "POST",
        "/api/life/memory/corrections",
        body={
            "target_kind": "experience_episode",
            "target_id": "episode-api-correction",
            "expected_revision": 1,
            "corrected_text": "The corrected governed date is Monday.",
            "source_evidence_ids": ["terminal-request-correction"],
            "idempotency_key": "correction-api-1",
        },
    )
    assert correction.status_code == 200, correction.text
    assert correction.json()["resource_id"] != "episode-api-correction"

    before_derived = memory_api.conversations.read_request_evidence(
        SESSION_ID, "request-derived-delete"
    )
    before_source = memory_api.conversations.read_request_evidence(
        SESSION_ID, "request-source-delete"
    )
    assert before_derived["redacted"] is False
    assert before_source["redacted"] is False

    statuses = []
    for suffix, episode_id, source_handling in (
        ("derived", "episode-api-derived-delete", "derived_only"),
        ("source", "episode-api-source-delete", "source_and_derived"),
    ):
        submitted = _request(
            memory_api,
            "POST",
            "/api/life/memory/deletions",
            body={
                "scope": "episode",
                "target_id": episode_id,
                "source_handling": source_handling,
                "reason_code": "user_requested",
                "idempotency_key": f"deletion-api-{suffix}",
            },
        )
        assert submitted.status_code == 200, submitted.text
        deletion_id = submitted.json()["resource_id"]
        progress = _request(
            memory_api,
            "GET",
            f"/api/life/memory/deletions/{deletion_id}",
        )
        assert progress.status_code == 200, progress.text
        payload = progress.json()
        assert payload["deletion_request_id"] == deletion_id
        assert payload["state"] == "verified"
        assert payload["source_handling"] == source_handling
        assert payload["target_count"] >= 1
        statuses.append((submitted, progress))

    after_derived = memory_api.conversations.read_request_evidence(
        SESSION_ID, "request-derived-delete"
    )
    after_source = memory_api.conversations.read_request_evidence(
        SESSION_ID, "request-source-delete"
    )
    assert after_derived["redacted"] is False
    assert after_source["redacted"] is True

    _assert_no_authority_leak(memory_api, correction.json())
    for submitted, progress in statuses:
        _assert_no_authority_leak(memory_api, submitted.json())
        _assert_no_authority_leak(memory_api, progress.json())


def test_deletion_progress_is_hidden_from_an_unbound_packaged_client(memory_api):
    submitted = _request(
        memory_api,
        "POST",
        "/api/life/memory/deletions",
        body={
            "scope": "episode",
            "target_id": "episode-api-derived-delete",
            "source_handling": "derived_only",
            "reason_code": "user_requested",
            "idempotency_key": "deletion-owner-isolation",
        },
    )
    assert submitted.status_code == 200, submitted.text

    other = memory_api.authority.issue("desktop-other", ALL_SCOPES)
    hidden = _request(
        memory_api,
        "GET",
        f"/api/life/memory/deletions/{submitted.json()['resource_id']}",
        token=other.token,
        session_id="session-memory-other",
    )

    assert hidden.status_code == 403
    assert _response_code(hidden) == "memory_guest_denied"
    _assert_no_authority_leak(memory_api, hidden.json())
