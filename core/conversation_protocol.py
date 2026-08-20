"""Wire protocol normalization for shared Javis conversations."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from core.life.memory.access import (
    ReservedAccessFieldError,
    ServerPrincipal,
    reject_reserved_client_access_fields,
)


CANONICAL_COMMANDS = {
    "conversation.attach",
    "conversation.message",
    "conversation.cancel",
    "conversation.confirm",
    "ping",
    "voice",
    "folder_file",
    "tool",
    "permission_change",
}
LEGACY_COMMANDS = {
    "message": "conversation.message",
    "cancel": "conversation.cancel",
    "confirm": "conversation.confirm",
}


class ConversationProtocolError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = str(code)
        self.message = str(message)
        super().__init__(self.message)

    def to_event(self) -> dict[str, Any]:
        return {
            "type": "protocol.error",
            "payload": {"code": self.code, "message": self.message},
        }


@dataclass(frozen=True)
class ClientCommand:
    type: str
    session_id: str
    request_id: str
    idempotency_key: str
    payload: dict[str, Any]
    voice_provenance: dict[str, Any] | None = None
    after_sequence: int = 0
    protocol_version: int = 1
    legacy: bool = False
    server_principal: ServerPrincipal | None = None


def normalize_client_message(
    message: dict[str, Any],
    default_session: str = "",
) -> ClientCommand:
    if not isinstance(message, dict):
        raise ConversationProtocolError("invalid_message", "message must be an object")
    raw_type = str(message.get("type") or "message").strip().lower()
    legacy = raw_type in LEGACY_COMMANDS or raw_type in {
        "voice",
        "folder_file",
        "tool",
        "permission_change",
        "ping",
    }
    command_type = LEGACY_COMMANDS.get(raw_type, raw_type)
    if command_type not in CANONICAL_COMMANDS:
        raise ConversationProtocolError("unknown_command", f"unknown command: {raw_type}")

    payload = message.get("payload", {})
    if not isinstance(payload, dict):
        raise ConversationProtocolError("invalid_payload", "payload must be an object")
    try:
        reject_reserved_client_access_fields(payload)
    except ReservedAccessFieldError as exc:
        raise ConversationProtocolError(
            "reserved_access_field",
            f"client field is server-owned: {exc.field_name}",
        ) from exc

    protocol_version = _bounded_int(
        payload.get("protocol_version", message.get("protocol_version", 1 if legacy else 2)),
        "invalid_protocol_version",
        minimum=1,
        maximum=2,
    )
    session_id = str(payload.get("session_id") or default_session or "").strip()
    request_id = str(payload.get("request_id") or "").strip()

    if command_type in {
        "conversation.attach",
        "conversation.message",
        "conversation.cancel",
        "conversation.confirm",
        "voice",
    }:
        session_id = _identifier(session_id, "session_id")
    elif session_id:
        session_id = _identifier(session_id, "session_id")

    if command_type in {"conversation.message", "voice"}:
        if not request_id and legacy:
            request_id = uuid.uuid4().hex
        request_id = _identifier(request_id, "request_id")
    elif request_id:
        request_id = _identifier(request_id, "request_id")

    if command_type == "conversation.message":
        text = str(payload.get("text") or "").strip()
        if not text:
            raise ConversationProtocolError("invalid_text", "message text is required")
        if len(text) > 100_000:
            raise ConversationProtocolError("invalid_text", "message text is too long")

    voice_provenance = None
    if "voice_provenance" in payload:
        if command_type != "conversation.message" or protocol_version != 2:
            raise ConversationProtocolError(
                "invalid_voice_provenance",
                "voice provenance is only supported by protocol v2 messages",
            )
        voice_provenance = _voice_provenance(payload.get("voice_provenance"))

    idempotency_key = str(payload.get("idempotency_key") or request_id or "").strip()
    if idempotency_key:
        idempotency_key = _identifier(idempotency_key, "idempotency_key")
    after_sequence = _bounded_int(
        payload.get("after_sequence", 0),
        "invalid_sequence",
        minimum=0,
        maximum=2_147_483_647,
    )
    return ClientCommand(
        type=command_type,
        session_id=session_id,
        request_id=request_id,
        idempotency_key=idempotency_key,
        payload=dict(payload),
        voice_provenance=voice_provenance,
        after_sequence=after_sequence,
        protocol_version=protocol_version,
        legacy=legacy or protocol_version == 1,
    )


def legacy_wire_events(event: dict[str, Any]) -> list[dict[str, Any]]:
    event_type = str(event.get("type") or "")
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    common = {
        "session_id": str(event.get("session_id") or ""),
        "request_id": str(event.get("request_id") or ""),
        "sequence": int(event.get("sequence") or 0),
    }
    if event_type.startswith("activity."):
        activity = event_type.split(".", 1)[1]
        if activity == "tool_started":
            return [{
                "type": "tool_start",
                "tool": str(payload.get("tool") or "unknown"),
                "params": payload.get("params") if isinstance(payload.get("params"), dict) else {},
                **common,
            }]
        if activity == "tool_completed":
            return [{
                "type": "tool_result",
                "tool": str(payload.get("tool") or "unknown"),
                "success": bool(payload.get("success", False)),
                "data": str(payload.get("data") or ""),
                **common,
            }]
        detail = str(payload.get("detail") or activity)
        return [{
            "type": "thinking",
            "content": detail,
            "detail": detail,
            "activity": activity,
            **common,
        }]
    if event_type == "response.delta":
        return [{"type": "text_delta", "text": str(payload.get("text") or ""), **common}]
    if event_type == "approval.required":
        return [{
            "type": "confirm_required",
            "approval_id": str(payload.get("approval_id") or ""),
            "tool": str(payload.get("tool") or "unknown"),
            "reason": str(payload.get("reason") or ""),
            "params": payload.get("params") if isinstance(payload.get("params"), dict) else {},
            **common,
        }]
    if event_type == "request.completed":
        return [{"type": "done", "detail": str(payload.get("detail") or "completed"), **common}]
    if event_type == "request.cancelled":
        return [{
            "type": "done",
            "detail": str(payload.get("reason") or "cancelled"),
            "cancelled": True,
            **common,
        }]
    if event_type == "request.failed":
        return [{"type": "error", "message": str(payload.get("error") or "request failed"), **common}]
    return []


def _identifier(value: Any, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized or len(normalized) > 256 or any(c in normalized for c in "\r\n\x00"):
        raise ConversationProtocolError(f"invalid_{field}", f"invalid {field}")
    return normalized


def _bounded_int(value: Any, code: str, *, minimum: int, maximum: int) -> int:
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise ConversationProtocolError(code, "value must be an integer") from exc
    if normalized < minimum or normalized > maximum:
        raise ConversationProtocolError(code, "integer is out of range")
    return normalized


def _voice_provenance(value: Any) -> dict[str, Any]:
    fields = {
        "runtime_boot_id",
        "session_id",
        "owner_generation",
        "voice_sequence",
        "voice_turn",
        "nonce",
        "proof",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise ConversationProtocolError(
            "invalid_voice_provenance", "voice provenance has an invalid schema"
        )
    normalized = {
        "runtime_boot_id": _identifier(value.get("runtime_boot_id"), "runtime_boot_id"),
        "session_id": _identifier(value.get("session_id"), "session_id"),
        "owner_generation": _bounded_int(
            value.get("owner_generation"),
            "invalid_voice_provenance",
            minimum=0,
            maximum=2_147_483_647,
        ),
        "voice_sequence": _bounded_int(
            value.get("voice_sequence"),
            "invalid_voice_provenance",
            minimum=0,
            maximum=2_147_483_647,
        ),
        "voice_turn": _bounded_int(
            value.get("voice_turn"),
            "invalid_voice_provenance",
            minimum=0,
            maximum=2_147_483_647,
        ),
        "nonce": str(value.get("nonce") or "").strip(),
        "proof": str(value.get("proof") or "").strip().lower(),
    }
    if (
        len(normalized["nonce"]) < 16
        or len(normalized["nonce"]) > 128
        or any(c in normalized["nonce"] for c in "\r\n\x00")
        or len(normalized["proof"]) != 64
        or any(c not in "0123456789abcdef" for c in normalized["proof"])
    ):
        raise ConversationProtocolError(
            "invalid_voice_provenance", "voice provenance has invalid proof material"
        )
    return normalized
