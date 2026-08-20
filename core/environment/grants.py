"""Server-owned authorization grants for governed environment observation.

Runtime capabilities establish that a request came from a trusted local client.
This module separately records the user's consent for a source, scope, and
purpose. Grant mutation is intentionally exposed only as ordinary Python service
methods; this module defines no model tool registration surface.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from core.environment.contracts import SourceKind


SCHEMA_VERSION = 1
MAX_GRANTS = 1_024
MAX_STORE_BYTES = 1_048_576
MAX_ID_CHARS = 256
MAX_SCOPE_CHARS = 2_048
MAX_PURPOSE_CHARS = 96
MAX_TTL_SECONDS = 366 * 24 * 60 * 60

_CONTROL_CHARACTER = re.compile(r"[\x00-\x1f\x7f]")
_PURPOSE = re.compile(r"[a-z0-9][a-z0-9._-]{0,95}")
_RFC3339_MILLISECONDS = re.compile(
    r"\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])"
    r"T(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d\.\d{3}Z"
)


class GrantDuration(str, Enum):
    ONCE = "once"
    SESSION = "session"
    PERSISTENT = "persistent"


class ModelVisibility(str, Enum):
    NONE = "none"
    LOCAL_ONLY = "local_only"
    GOVERNED_CLOUD = "governed_cloud"


class ModelTarget(str, Enum):
    NONE = "none"
    LOCAL = "local"
    GOVERNED_CLOUD = "governed_cloud"


class EnvironmentGrantStoreError(RuntimeError):
    """Fail-closed storage or validation error."""


class EnvironmentGrantConflictError(EnvironmentGrantStoreError):
    """A grant identifier already exists."""


def _bounded_text(value: Any, field_name: str, *, maximum: int) -> str:
    if type(value) is not str:
        raise ValueError(f"{field_name} must be a string")
    normalized = value.strip()
    if (
        not normalized
        or len(normalized) > maximum
        or _CONTROL_CHARACTER.search(normalized)
    ):
        raise ValueError(f"{field_name} must be a bounded non-empty string")
    try:
        normalized.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError(f"{field_name} must be valid UTF-8") from exc
    return normalized


def _timestamp(value: datetime) -> str:
    milliseconds = value.microsecond // 1_000
    return value.strftime("%Y-%m-%dT%H:%M:%S.") + f"{milliseconds:03d}Z"


def _parse_timestamp(value: Any, field_name: str) -> datetime:
    if type(value) is not str or _RFC3339_MILLISECONDS.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be RFC3339 UTC with millisecond precision")
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError as exc:
        raise ValueError(f"{field_name} must be a valid UTC timestamp") from exc


def _enum(value: Any, enum_type: type[Enum], field_name: str) -> Enum:
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} is not supported") from exc


def grant_id_hash(grant_id: str) -> str:
    normalized = _bounded_text(grant_id, "grant_id", maximum=MAX_ID_CHARS)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class EnvironmentGrant:
    schema_version: int
    grant_id: str
    owner_subject_id: str
    source_kind: SourceKind
    scope: str
    purpose: str
    duration: GrantDuration
    runtime_boot_id: str
    session_id: str | None
    issued_at_utc: str
    expires_at_utc: str
    foreground_only: bool
    model_visibility: ModelVisibility
    media_egress_allowed: bool
    revoked_at_utc: str | None = None

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != SCHEMA_VERSION:
            raise ValueError(f"schema_version must equal {SCHEMA_VERSION}")
        object.__setattr__(
            self, "grant_id", _bounded_text(self.grant_id, "grant_id", maximum=MAX_ID_CHARS)
        )
        object.__setattr__(
            self,
            "owner_subject_id",
            _bounded_text(
                self.owner_subject_id, "owner_subject_id", maximum=MAX_ID_CHARS
            ),
        )
        object.__setattr__(
            self, "source_kind", _enum(self.source_kind, SourceKind, "source_kind")
        )
        object.__setattr__(
            self, "scope", _bounded_text(self.scope, "scope", maximum=MAX_SCOPE_CHARS)
        )
        purpose = _bounded_text(self.purpose, "purpose", maximum=MAX_PURPOSE_CHARS)
        if _PURPOSE.fullmatch(purpose) is None:
            raise ValueError("purpose must be a lowercase machine code")
        object.__setattr__(self, "purpose", purpose)
        object.__setattr__(
            self, "duration", _enum(self.duration, GrantDuration, "duration")
        )
        object.__setattr__(
            self,
            "runtime_boot_id",
            _bounded_text(
                self.runtime_boot_id, "runtime_boot_id", maximum=MAX_ID_CHARS
            ),
        )
        if self.session_id is not None:
            object.__setattr__(
                self,
                "session_id",
                _bounded_text(self.session_id, "session_id", maximum=MAX_ID_CHARS),
            )
        if self.duration in (GrantDuration.ONCE, GrantDuration.SESSION):
            if self.session_id is None:
                raise ValueError("once and session grants require session_id")
        elif self.session_id is not None:
            raise ValueError("persistent grants must not bind a session_id")

        issued_at = _parse_timestamp(self.issued_at_utc, "issued_at_utc")
        expires_at = _parse_timestamp(self.expires_at_utc, "expires_at_utc")
        if expires_at <= issued_at:
            raise ValueError("expires_at_utc must follow issued_at_utc")
        if (expires_at - issued_at).total_seconds() > MAX_TTL_SECONDS:
            raise ValueError(f"grant TTL must be at most {MAX_TTL_SECONDS} seconds")
        if type(self.foreground_only) is not bool:
            raise ValueError("foreground_only must be a boolean")
        object.__setattr__(
            self,
            "model_visibility",
            _enum(self.model_visibility, ModelVisibility, "model_visibility"),
        )
        if type(self.media_egress_allowed) is not bool:
            raise ValueError("media_egress_allowed must be a boolean")
        if (
            self.media_egress_allowed
            and self.model_visibility is not ModelVisibility.GOVERNED_CLOUD
        ):
            raise ValueError(
                "media egress requires governed_cloud model visibility"
            )
        if self.revoked_at_utc is not None:
            revoked_at = _parse_timestamp(self.revoked_at_utc, "revoked_at_utc")
            if revoked_at < issued_at:
                raise ValueError("revoked_at_utc must not precede issued_at_utc")

    @property
    def id_hash(self) -> str:
        return grant_id_hash(self.grant_id)

    def to_persistent_dict(self) -> dict[str, Any]:
        if self.duration is not GrantDuration.PERSISTENT:
            raise ValueError("only persistent grants can be serialized")
        return {
            "schema_version": self.schema_version,
            "grant_id": self.grant_id,
            "owner_subject_id": self.owner_subject_id,
            "source_kind": self.source_kind.value,
            "scope": self.scope,
            "purpose": self.purpose,
            "duration": self.duration.value,
            "runtime_boot_id": self.runtime_boot_id,
            "session_id": None,
            "issued_at_utc": self.issued_at_utc,
            "expires_at_utc": self.expires_at_utc,
            "foreground_only": self.foreground_only,
            "model_visibility": self.model_visibility.value,
            "media_egress_allowed": self.media_egress_allowed,
            "revoked_at_utc": self.revoked_at_utc,
        }

    def safe_projection(self, *, active: bool) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "grant_id_hash": self.id_hash,
            "owner_subject_id_hash": hashlib.sha256(
                self.owner_subject_id.encode("utf-8")
            ).hexdigest(),
            "source_kind": self.source_kind.value,
            "scope_hash": hashlib.sha256(self.scope.encode("utf-8")).hexdigest(),
            "purpose": self.purpose,
            "duration": self.duration.value,
            "expires_at_utc": self.expires_at_utc,
            "foreground_only": self.foreground_only,
            "model_visibility": self.model_visibility.value,
            "media_egress_allowed": self.media_egress_allowed,
            "revoked": self.revoked_at_utc is not None,
            "active": bool(active),
        }


@dataclass(frozen=True, slots=True)
class EnvironmentGrantDecision:
    allowed: bool
    reason_code: str
    grant_id_hash: str
    source_kind: SourceKind
    purpose: str
    model_visible: bool = False
    media_egress_allowed: bool = False
    consumed: bool = False

    def safe_projection(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reason_code": self.reason_code,
            "grant_id_hash": self.grant_id_hash,
            "source_kind": self.source_kind.value,
            "purpose": self.purpose,
            "model_visible": self.model_visible,
            "media_egress_allowed": self.media_egress_allowed,
            "consumed": self.consumed,
        }


class EnvironmentGrantStore:
    """Thread-safe, server-owned environment consent authority."""

    def __init__(
        self,
        data_root: str | Path,
        *,
        runtime_boot_id: str,
        now: Callable[[], float | datetime] | None = None,
        grant_id_factory: Callable[[], str] | None = None,
        max_grants: int = MAX_GRANTS,
    ) -> None:
        try:
            root = Path(data_root).expanduser().resolve()
        except (OSError, TypeError, ValueError) as exc:
            raise ValueError("data_root is invalid") from exc
        if root.exists() and not root.is_dir():
            raise ValueError("data_root must be a directory")
        if type(max_grants) is not int or not 1 <= max_grants <= MAX_GRANTS:
            raise ValueError(f"max_grants must be between 1 and {MAX_GRANTS}")
        self.data_root = root
        self.storage_path = root / "environment" / "grants.v1.json"
        self._runtime_boot_id = _bounded_text(
            runtime_boot_id, "runtime_boot_id", maximum=MAX_ID_CHARS
        )
        self._now = now or time.time
        self._grant_id_factory = grant_id_factory or (
            lambda: f"environment-grant-{uuid.uuid4().hex}"
        )
        self._max_grants = max_grants
        self._lock = threading.RLock()
        self._revision = 0
        self._grants: dict[str, EnvironmentGrant] = {}
        self._persistent_activations: set[str] = set()
        self._load()

    @property
    def runtime_boot_id(self) -> str:
        with self._lock:
            return self._runtime_boot_id

    def issue(
        self,
        *,
        owner_subject_id: str,
        source_kind: SourceKind | str,
        scope: str,
        purpose: str,
        duration: GrantDuration | str,
        ttl_seconds: int,
        session_id: str | None = None,
        foreground_only: bool = False,
        model_visibility: ModelVisibility | str = ModelVisibility.NONE,
        media_egress_allowed: bool = False,
    ) -> EnvironmentGrant:
        if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= MAX_TTL_SECONDS:
            raise ValueError(f"ttl_seconds must be between 1 and {MAX_TTL_SECONDS}")
        now = self._clock()
        identifier = _bounded_text(
            self._grant_id_factory(), "grant_id", maximum=MAX_ID_CHARS
        )
        grant = EnvironmentGrant(
            schema_version=SCHEMA_VERSION,
            grant_id=identifier,
            owner_subject_id=owner_subject_id,
            source_kind=source_kind,
            scope=scope,
            purpose=purpose,
            duration=duration,
            runtime_boot_id=self.runtime_boot_id,
            session_id=session_id,
            issued_at_utc=_timestamp(now),
            expires_at_utc=_timestamp(
                datetime.fromtimestamp(now.timestamp() + ttl_seconds, timezone.utc)
            ),
            foreground_only=foreground_only,
            model_visibility=model_visibility,
            media_egress_allowed=media_egress_allowed,
        )
        with self._lock:
            self._purge_volatile_locked(now)
            if identifier in self._grants:
                raise EnvironmentGrantConflictError("grant_id_conflict")
            if len(self._grants) >= self._max_grants:
                raise EnvironmentGrantStoreError("grant_capacity_reached")
            candidate = dict(self._grants)
            candidate[identifier] = grant
            if grant.duration is GrantDuration.PERSISTENT:
                self._persist_candidate_locked(candidate, self._revision + 1)
            self._grants = candidate
            if grant.duration is GrantDuration.PERSISTENT:
                self._revision += 1
                self._persistent_activations.add(identifier)
            return grant

    def get(self, grant_id: str) -> EnvironmentGrant | None:
        identifier = _bounded_text(grant_id, "grant_id", maximum=MAX_ID_CHARS)
        now = self._clock()
        with self._lock:
            self._purge_volatile_locked(now)
            return self._grants.get(identifier)

    def list_for_owner(self, owner_subject_id: str) -> tuple[EnvironmentGrant, ...]:
        owner = _bounded_text(
            owner_subject_id, "owner_subject_id", maximum=MAX_ID_CHARS
        )
        now = self._clock()
        with self._lock:
            self._purge_volatile_locked(now)
            return tuple(
                sorted(
                    (
                        grant
                        for grant in self._grants.values()
                        if grant.owner_subject_id == owner
                    ),
                    key=lambda grant: (grant.issued_at_utc, grant.grant_id),
                )
            )

    def safe_status_for_owner(self, owner_subject_id: str) -> tuple[dict[str, Any], ...]:
        with self._lock:
            grants = self.list_for_owner(owner_subject_id)
            return tuple(
                grant.safe_projection(active=self._is_active_locked(grant))
                for grant in grants
            )

    def activate_persistent(self, grant_id: str, *, owner_subject_id: str) -> bool:
        identifier = _bounded_text(grant_id, "grant_id", maximum=MAX_ID_CHARS)
        owner = _bounded_text(
            owner_subject_id, "owner_subject_id", maximum=MAX_ID_CHARS
        )
        now = self._clock()
        with self._lock:
            grant = self._grants.get(identifier)
            if (
                grant is None
                or grant.owner_subject_id != owner
                or grant.duration is not GrantDuration.PERSISTENT
                or grant.revoked_at_utc is not None
                or _parse_timestamp(grant.expires_at_utc, "expires_at_utc") <= now
            ):
                return False
            self._persistent_activations.add(identifier)
            return True

    def authorize(
        self,
        grant_id: str,
        *,
        owner_subject_id: str,
        source_kind: SourceKind | str,
        scope: str,
        purpose: str,
        session_id: str | None = None,
        is_foreground: bool,
        model_target: ModelTarget | str = ModelTarget.NONE,
        media_egress: bool = False,
        consume_once: bool = True,
    ) -> EnvironmentGrantDecision:
        identifier = _bounded_text(grant_id, "grant_id", maximum=MAX_ID_CHARS)
        owner = _bounded_text(
            owner_subject_id, "owner_subject_id", maximum=MAX_ID_CHARS
        )
        expected_source = _enum(source_kind, SourceKind, "source_kind")
        expected_scope = _bounded_text(scope, "scope", maximum=MAX_SCOPE_CHARS)
        expected_purpose = _bounded_text(
            purpose, "purpose", maximum=MAX_PURPOSE_CHARS
        )
        if _PURPOSE.fullmatch(expected_purpose) is None:
            raise ValueError("purpose must be a lowercase machine code")
        if session_id is not None:
            session_id = _bounded_text(
                session_id, "session_id", maximum=MAX_ID_CHARS
            )
        if type(is_foreground) is not bool:
            raise ValueError("is_foreground must be a boolean")
        target = _enum(model_target, ModelTarget, "model_target")
        if type(media_egress) is not bool:
            raise ValueError("media_egress must be a boolean")
        if type(consume_once) is not bool:
            raise ValueError("consume_once must be a boolean")

        now = self._clock()
        digest = grant_id_hash(identifier)
        with self._lock:
            grant = self._grants.get(identifier)
            if grant is None:
                return self._decision(
                    False, "grant_not_found", digest, expected_source, expected_purpose
                )
            reason = self._deny_reason_locked(
                grant,
                now=now,
                owner_subject_id=owner,
                source_kind=expected_source,
                scope=expected_scope,
                purpose=expected_purpose,
                session_id=session_id,
                is_foreground=is_foreground,
                model_target=target,
                media_egress=media_egress,
            )
            if reason is not None:
                return self._decision(
                    False, reason, grant.id_hash, expected_source, expected_purpose
                )

            model_visible = (
                target is not ModelTarget.NONE
                and self._model_visible(grant.model_visibility, target)
            )
            consumed = grant.duration is GrantDuration.ONCE and consume_once
            if consumed:
                self._grants.pop(identifier, None)
            return self._decision(
                True,
                "authorized",
                grant.id_hash,
                grant.source_kind,
                grant.purpose,
                model_visible=model_visible,
                media_egress_allowed=(
                    media_egress and grant.media_egress_allowed
                ),
                consumed=consumed,
            )

    def check(self, grant_id: str, **kwargs: Any) -> EnvironmentGrantDecision:
        """Evaluate without consuming a once grant."""

        if "consume_once" in kwargs:
            raise ValueError("check does not accept consume_once")
        return self.authorize(grant_id, consume_once=False, **kwargs)

    def revoke(self, grant_id: str, *, owner_subject_id: str) -> bool:
        identifier = _bounded_text(grant_id, "grant_id", maximum=MAX_ID_CHARS)
        owner = _bounded_text(
            owner_subject_id, "owner_subject_id", maximum=MAX_ID_CHARS
        )
        now = self._clock()
        with self._lock:
            grant = self._grants.get(identifier)
            if grant is None or grant.owner_subject_id != owner:
                return False
            if grant.revoked_at_utc is not None:
                return True
            if grant.duration is not GrantDuration.PERSISTENT:
                self._grants.pop(identifier, None)
                return True

            revoked = replace(grant, revoked_at_utc=_timestamp(now))
            candidate = dict(self._grants)
            candidate[identifier] = revoked
            self._persist_candidate_locked(candidate, self._revision + 1)
            self._grants = candidate
            self._revision += 1
            self._persistent_activations.discard(identifier)
            return True

    def close_session(self, session_id: str) -> int:
        session = _bounded_text(session_id, "session_id", maximum=MAX_ID_CHARS)
        with self._lock:
            identifiers = tuple(
                grant_id
                for grant_id, grant in self._grants.items()
                if grant.session_id == session
                and grant.duration in (GrantDuration.ONCE, GrantDuration.SESSION)
            )
            for grant_id in identifiers:
                self._grants.pop(grant_id, None)
            return len(identifiers)

    def rotate_boot(self, runtime_boot_id: str) -> int:
        boot = _bounded_text(
            runtime_boot_id, "runtime_boot_id", maximum=MAX_ID_CHARS
        )
        with self._lock:
            volatile = tuple(
                grant_id
                for grant_id, grant in self._grants.items()
                if grant.duration is not GrantDuration.PERSISTENT
            )
            for grant_id in volatile:
                self._grants.pop(grant_id, None)
            self._persistent_activations.clear()
            self._runtime_boot_id = boot
            return len(volatile)

    def _deny_reason_locked(
        self,
        grant: EnvironmentGrant,
        *,
        now: datetime,
        owner_subject_id: str,
        source_kind: SourceKind,
        scope: str,
        purpose: str,
        session_id: str | None,
        is_foreground: bool,
        model_target: ModelTarget,
        media_egress: bool,
    ) -> str | None:
        if grant.owner_subject_id != owner_subject_id:
            return "owner_mismatch"
        if grant.revoked_at_utc is not None:
            return "grant_revoked"
        if _parse_timestamp(grant.expires_at_utc, "expires_at_utc") <= now:
            return "grant_expired"
        if grant.source_kind is not source_kind:
            return "source_mismatch"
        if grant.scope != scope:
            return "scope_mismatch"
        if grant.purpose != purpose:
            return "purpose_mismatch"
        if grant.duration in (GrantDuration.ONCE, GrantDuration.SESSION):
            if grant.runtime_boot_id != self._runtime_boot_id:
                return "runtime_boot_mismatch"
            if session_id is None or grant.session_id != session_id:
                return "session_mismatch"
        elif grant.grant_id not in self._persistent_activations:
            return "persistent_reactivation_required"
        if grant.foreground_only and not is_foreground:
            return "foreground_required"
        if not self._model_visible(grant.model_visibility, model_target):
            return "model_visibility_denied"
        if media_egress and model_target is not ModelTarget.GOVERNED_CLOUD:
            return "media_egress_target_required"
        if media_egress and not grant.media_egress_allowed:
            return "media_egress_denied"
        return None

    @staticmethod
    def _model_visible(
        visibility: ModelVisibility, target: ModelTarget
    ) -> bool:
        if target is ModelTarget.NONE:
            return True
        if target is ModelTarget.LOCAL:
            return visibility in (
                ModelVisibility.LOCAL_ONLY,
                ModelVisibility.GOVERNED_CLOUD,
            )
        return visibility is ModelVisibility.GOVERNED_CLOUD

    @staticmethod
    def _decision(
        allowed: bool,
        reason_code: str,
        digest: str,
        source_kind: SourceKind,
        purpose: str,
        *,
        model_visible: bool = False,
        media_egress_allowed: bool = False,
        consumed: bool = False,
    ) -> EnvironmentGrantDecision:
        return EnvironmentGrantDecision(
            allowed=allowed,
            reason_code=reason_code,
            grant_id_hash=digest,
            source_kind=source_kind,
            purpose=purpose,
            model_visible=model_visible,
            media_egress_allowed=media_egress_allowed,
            consumed=consumed,
        )

    def _clock(self) -> datetime:
        value = self._now()
        if isinstance(value, datetime):
            if value.tzinfo is None or value.utcoffset() is None:
                raise RuntimeError("grant clock must return an aware datetime")
            return value.astimezone(timezone.utc)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise RuntimeError("grant clock must return a finite epoch or datetime")
        epoch = float(value)
        if not math.isfinite(epoch):
            raise RuntimeError("grant clock must return a finite epoch or datetime")
        try:
            return datetime.fromtimestamp(epoch, timezone.utc)
        except (OverflowError, OSError, ValueError) as exc:
            raise RuntimeError("grant clock returned an invalid epoch") from exc

    def _is_active_locked(self, grant: EnvironmentGrant) -> bool:
        now = self._clock()
        if (
            grant.revoked_at_utc is not None
            or _parse_timestamp(grant.expires_at_utc, "expires_at_utc") <= now
        ):
            return False
        if grant.duration is GrantDuration.PERSISTENT:
            return grant.grant_id in self._persistent_activations
        return (
            grant.runtime_boot_id == self._runtime_boot_id
            and grant.session_id is not None
        )

    def _purge_volatile_locked(self, now: datetime) -> None:
        expired = tuple(
            grant_id
            for grant_id, grant in self._grants.items()
            if grant.duration is not GrantDuration.PERSISTENT
            and _parse_timestamp(grant.expires_at_utc, "expires_at_utc") <= now
        )
        for grant_id in expired:
            self._grants.pop(grant_id, None)

    def _load(self) -> None:
        path = self.storage_path
        if not path.exists():
            return
        try:
            self._assert_storage_path_safe()
            if not path.is_file() or path.is_symlink():
                raise EnvironmentGrantStoreError("grant_store_path_unsafe")
            size = path.stat().st_size
            if size > MAX_STORE_BYTES:
                raise EnvironmentGrantStoreError("grant_store_too_large")
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, Mapping):
                raise ValueError("root must be an object")
            if set(payload) != {"schema_version", "revision", "grants"}:
                raise ValueError("unknown or missing root fields")
            if payload["schema_version"] != SCHEMA_VERSION:
                raise ValueError("unsupported schema version")
            revision = payload["revision"]
            rows = payload["grants"]
            if type(revision) is not int or revision < 0:
                raise ValueError("revision must be a non-negative integer")
            if not isinstance(rows, list) or len(rows) > self._max_grants:
                raise ValueError("grants must be a bounded array")
            loaded: dict[str, EnvironmentGrant] = {}
            expected_fields = {
                "schema_version",
                "grant_id",
                "owner_subject_id",
                "source_kind",
                "scope",
                "purpose",
                "duration",
                "runtime_boot_id",
                "session_id",
                "issued_at_utc",
                "expires_at_utc",
                "foreground_only",
                "model_visibility",
                "media_egress_allowed",
                "revoked_at_utc",
            }
            for row in rows:
                if not isinstance(row, Mapping) or set(row) != expected_fields:
                    raise ValueError("grant has unknown or missing fields")
                grant = EnvironmentGrant(**dict(row))
                if grant.duration is not GrantDuration.PERSISTENT:
                    raise ValueError("only persistent grants may be stored")
                if grant.grant_id in loaded:
                    raise ValueError("duplicate grant_id")
                loaded[grant.grant_id] = grant
        except EnvironmentGrantStoreError:
            raise
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            raise EnvironmentGrantStoreError("grant_store_invalid") from exc
        self._revision = revision
        self._grants = loaded

    def _persist_candidate_locked(
        self, candidate: Mapping[str, EnvironmentGrant], revision: int
    ) -> None:
        persistent = sorted(
            (
                grant.to_persistent_dict()
                for grant in candidate.values()
                if grant.duration is GrantDuration.PERSISTENT
            ),
            key=lambda row: str(row["grant_id"]),
        )
        payload = {
            "schema_version": SCHEMA_VERSION,
            "revision": revision,
            "grants": persistent,
        }
        encoded = (
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        if len(encoded) > MAX_STORE_BYTES:
            raise EnvironmentGrantStoreError("grant_store_too_large")

        path = self.storage_path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._assert_storage_path_safe()
        if path.exists() and path.is_symlink():
            raise EnvironmentGrantStoreError("grant_store_path_unsafe")
        descriptor, temporary_name = tempfile.mkstemp(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.chmod(temporary, 0o600)
            except OSError:
                pass
            os.replace(temporary, path)
            self._sync_directory(path.parent)
        except Exception as exc:
            temporary.unlink(missing_ok=True)
            if isinstance(exc, EnvironmentGrantStoreError):
                raise
            raise EnvironmentGrantStoreError("grant_store_write_failed") from exc

    def _assert_storage_path_safe(self) -> None:
        try:
            parent = self.storage_path.parent.resolve(strict=True)
            parent.relative_to(self.data_root)
        except (OSError, ValueError) as exc:
            raise EnvironmentGrantStoreError("grant_store_path_unsafe") from exc

    @staticmethod
    def _sync_directory(directory: Path) -> None:
        if os.name == "nt":
            return
        descriptor = None
        try:
            descriptor = os.open(directory, os.O_RDONLY)
            os.fsync(descriptor)
        except OSError:
            return
        finally:
            if descriptor is not None:
                os.close(descriptor)


__all__ = [
    "EnvironmentGrant",
    "EnvironmentGrantConflictError",
    "EnvironmentGrantDecision",
    "EnvironmentGrantStore",
    "EnvironmentGrantStoreError",
    "GrantDuration",
    "ModelTarget",
    "ModelVisibility",
    "grant_id_hash",
]
