"""Pure L3 subject and session command contracts.

Authority is resolved before these values are constructed.  The contracts carry
the server-built access context but never a client-selected actor, assurance,
audience, permission, or capability token.
"""

from __future__ import annotations

from dataclasses import dataclass

from .contracts import (
    MAX_TITLE_CHARS,
    AccessContext,
    ActorKind,
    _WireContract,
    _boolean,
    _contract,
    _id,
    _non_negative_int,
    _schema,
    _text,
    _text_tuple,
    _timestamp,
)


def _command_common(
    schema_version: int,
    command_id: str,
    access_context: AccessContext,
    idempotency_key: str,
    issued_at_utc: str,
) -> AccessContext:
    _schema(schema_version)
    _id(command_id, "command_id")
    context = _contract(access_context, AccessContext, "access_context")
    _id(idempotency_key, "idempotency_key")
    _timestamp(issued_at_utc, "issued_at_utc")
    return context


@dataclass(frozen=True)
class BootstrapPrimary(_WireContract):
    schema_version: int
    command_id: str
    access_context: AccessContext
    display_name: str
    aliases: tuple[str, ...]
    explicit_confirmation: bool
    idempotency_key: str
    issued_at_utc: str

    def __post_init__(self) -> None:
        context = _command_common(
            self.schema_version,
            self.command_id,
            self.access_context,
            self.idempotency_key,
            self.issued_at_utc,
        )
        object.__setattr__(self, "access_context", context)
        _text(self.display_name, "display_name", max_chars=MAX_TITLE_CHARS)
        object.__setattr__(
            self,
            "aliases",
            _text_tuple(
                self.aliases,
                "aliases",
                maximum=16,
                max_chars=MAX_TITLE_CHARS,
            ),
        )
        _boolean(self.explicit_confirmation, "explicit_confirmation")
        if not self.explicit_confirmation:
            raise ValueError("explicit_confirmation: primary bootstrap must be explicit")
        if "identity.manage" not in context.capability_scopes:
            raise ValueError("access_context: identity.manage scope is required")


@dataclass(frozen=True)
class CreateKnownPerson(_WireContract):
    schema_version: int
    command_id: str
    access_context: AccessContext
    display_name: str
    aliases: tuple[str, ...]
    explicit_confirmation: bool
    idempotency_key: str
    issued_at_utc: str

    def __post_init__(self) -> None:
        context = _command_common(
            self.schema_version,
            self.command_id,
            self.access_context,
            self.idempotency_key,
            self.issued_at_utc,
        )
        object.__setattr__(self, "access_context", context)
        _text(self.display_name, "display_name", max_chars=MAX_TITLE_CHARS)
        object.__setattr__(
            self,
            "aliases",
            _text_tuple(
                self.aliases,
                "aliases",
                maximum=16,
                max_chars=MAX_TITLE_CHARS,
            ),
        )
        _boolean(self.explicit_confirmation, "explicit_confirmation")
        if not self.explicit_confirmation:
            raise ValueError("explicit_confirmation: known person creation must be explicit")
        if "identity.manage" not in context.capability_scopes:
            raise ValueError("access_context: identity.manage scope is required")
        if context.actor_kind is not ActorKind.PRIMARY_USER or context.binding_id is None:
            raise ValueError("access_context: active primary binding is required")


@dataclass(frozen=True)
class BindSession(_WireContract):
    schema_version: int
    command_id: str
    access_context: AccessContext
    target_subject_id: str
    expected_generation: int
    explicit_confirmation: bool
    idempotency_key: str
    issued_at_utc: str

    def __post_init__(self) -> None:
        context = _command_common(
            self.schema_version,
            self.command_id,
            self.access_context,
            self.idempotency_key,
            self.issued_at_utc,
        )
        object.__setattr__(self, "access_context", context)
        _id(self.target_subject_id, "target_subject_id")
        _non_negative_int(self.expected_generation, "expected_generation")
        _boolean(self.explicit_confirmation, "explicit_confirmation")
        if not self.explicit_confirmation:
            raise ValueError("explicit_confirmation: session binding must be explicit")
        if "participants.manage" not in context.capability_scopes:
            raise ValueError("access_context: participants.manage scope is required")


@dataclass(frozen=True)
class HandoffSession(_WireContract):
    schema_version: int
    command_id: str
    access_context: AccessContext
    target_subject_id: str
    lease_id: str
    expected_generation: int
    explicit_confirmation: bool
    idempotency_key: str
    issued_at_utc: str

    def __post_init__(self) -> None:
        context = _command_common(
            self.schema_version,
            self.command_id,
            self.access_context,
            self.idempotency_key,
            self.issued_at_utc,
        )
        object.__setattr__(self, "access_context", context)
        _id(self.target_subject_id, "target_subject_id")
        _id(self.lease_id, "lease_id")
        _non_negative_int(self.expected_generation, "expected_generation")
        _boolean(self.explicit_confirmation, "explicit_confirmation")
        if not self.explicit_confirmation:
            raise ValueError("explicit_confirmation: session handoff must be explicit")
        if "participants.manage" not in context.capability_scopes:
            raise ValueError("access_context: participants.manage scope is required")
        if context.actor_kind is not ActorKind.PRIMARY_USER or context.binding_id is None:
            raise ValueError("access_context: active primary binding is required")


@dataclass(frozen=True)
class SetGuestPresent(_WireContract):
    schema_version: int
    command_id: str
    access_context: AccessContext
    guest_present: bool
    expected_generation: int
    explicit_owner_confirmation: bool
    idempotency_key: str
    issued_at_utc: str

    def __post_init__(self) -> None:
        context = _command_common(
            self.schema_version,
            self.command_id,
            self.access_context,
            self.idempotency_key,
            self.issued_at_utc,
        )
        object.__setattr__(self, "access_context", context)
        _boolean(self.guest_present, "guest_present")
        _boolean(self.explicit_owner_confirmation, "explicit_owner_confirmation")
        _non_negative_int(self.expected_generation, "expected_generation")
        if not self.guest_present and not self.explicit_owner_confirmation:
            raise ValueError(
                "explicit_owner_confirmation: clearing guest presence requires confirmation"
            )
        if "participants.manage" not in context.capability_scopes:
            raise ValueError("access_context: participants.manage scope is required")


@dataclass(frozen=True)
class LockSession(_WireContract):
    schema_version: int
    command_id: str
    access_context: AccessContext
    expected_generation: int
    idempotency_key: str
    issued_at_utc: str

    def __post_init__(self) -> None:
        context = _command_common(
            self.schema_version,
            self.command_id,
            self.access_context,
            self.idempotency_key,
            self.issued_at_utc,
        )
        object.__setattr__(self, "access_context", context)
        _non_negative_int(self.expected_generation, "expected_generation")
        if "participants.manage" not in context.capability_scopes:
            raise ValueError("access_context: participants.manage scope is required")


@dataclass(frozen=True)
class DisableSubject(_WireContract):
    schema_version: int
    command_id: str
    access_context: AccessContext
    target_subject_id: str
    expected_revision: int
    explicit_confirmation: bool
    idempotency_key: str
    issued_at_utc: str

    def __post_init__(self) -> None:
        context = _command_common(
            self.schema_version,
            self.command_id,
            self.access_context,
            self.idempotency_key,
            self.issued_at_utc,
        )
        object.__setattr__(self, "access_context", context)
        _id(self.target_subject_id, "target_subject_id")
        _non_negative_int(self.expected_revision, "expected_revision")
        if self.expected_revision < 1:
            raise ValueError("expected_revision: must be positive")
        _boolean(self.explicit_confirmation, "explicit_confirmation")
        if not self.explicit_confirmation:
            raise ValueError("explicit_confirmation: subject disable must be explicit")
        if "identity.manage" not in context.capability_scopes:
            raise ValueError("access_context: identity.manage scope is required")
        if context.actor_kind is not ActorKind.PRIMARY_USER or context.binding_id is None:
            raise ValueError("access_context: active primary binding is required")


__all__ = [
    "BindSession",
    "BootstrapPrimary",
    "CreateKnownPerson",
    "DisableSubject",
    "HandoffSession",
    "LockSession",
    "SetGuestPresent",
]
