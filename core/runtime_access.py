"""Process-local authorization for Javis desktop runtime surfaces."""

from __future__ import annotations

import base64
import asyncio
import hashlib
import hmac
import ipaddress
import math
import re
import secrets
import threading
import time
from collections import Counter, OrderedDict, deque
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field as dataclass_field
from typing import Any


ENVIRONMENT_RUNTIME_ACCESS_SCOPES = frozenset(
    {
        "camera.capture",
        "environment.observe",
        "environment.read",
        "screen.capture",
        "workspace.read",
    }
)
MEMORY_RUNTIME_ACCESS_SCOPES = frozenset(
    {
        "memory.delete",
        "memory.manage",
        "memory.migrate",
        "memory.read",
    }
)
ACTION_RUNTIME_ACCESS_SCOPES = frozenset(
    {
        "action.approve",
        "action.execute",
        "action.read",
        "action.recover",
        "config.read",
        "config.write",
        "control.cancel",
        "control.fuse.reset",
        "control.fuse.trip",
        "control.read",
        "control.root.issue",
        "conversation.cancel",
        "conversation.read",
        "conversation.write",
        "diagnostics.run",
        "environment.grant",
        "evolution.write",
        "intent.read",
        "intent.write",
        "memory.write",
        "models.install",
        "permission.write",
        "runtime.shutdown",
        "skills.write",
        "workspace.write",
    }
)
RUNTIME_ACCESS_SCOPES = frozenset(
    {
        # Explicit compatibility scope for clients shipped before L6. New
        # clients receive conversation.read/conversation.write separately.
        "conversation",
        "diagnostics.read",
        "life.read",
        "playback",
        "voice.capture",
    }
) | ACTION_RUNTIME_ACCESS_SCOPES | ENVIRONMENT_RUNTIME_ACCESS_SCOPES | MEMORY_RUNTIME_ACCESS_SCOPES
PACKAGED_ORIGINS = frozenset(
    {
        "tauri://localhost",
        "http://tauri.localhost",
        "https://tauri.localhost",
    }
)
DEVELOPMENT_ORIGINS = frozenset(
    {
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    }
)

_TOKEN_PROTOCOL_PREFIX = "javis-capability."
_BASE_PROTOCOL = "javis-runtime-v1"
_TOKEN_VALUE = re.compile(r"[A-Za-z0-9_-]{16,256}")
_CONTROL_CHARACTER = re.compile(r"[\x00-\x1f\x7f]")


@dataclass(frozen=True)
class IssuedRuntimeCapability:
    token: str
    runtime_boot_id: str
    client_instance_id: str
    scopes: tuple[str, ...]
    issued_at_epoch: float
    expires_at_epoch: float
    nonce: str


@dataclass(frozen=True)
class RuntimeAccessDecision:
    allowed: bool
    reason_code: str
    close_code: int
    scope: str
    client_id_hash: str
    expires_in_seconds: float = 0.0
    nonce_digest: str = ""
    principal: "RuntimeAccessPrincipal | None" = None


@dataclass(frozen=True, slots=True)
class RuntimeAccessPrincipal:
    runtime_boot_id: str
    client_id_hash: str
    scopes: tuple[str, ...]
    issued_at_epoch: float
    expires_at_epoch: float
    binding_source: str

    def safe_projection(self) -> dict[str, Any]:
        return {
            "runtime_boot_id": self.runtime_boot_id,
            "client_id_hash": self.client_id_hash,
            "scopes": list(self.scopes),
            "issued_at_epoch": self.issued_at_epoch,
            "expires_at_epoch": self.expires_at_epoch,
            "binding_source": self.binding_source,
        }


@dataclass(frozen=True, slots=True)
class RuntimeAccessSession:
    """Bearer-free handle to a capability retained only by this process."""

    session_ref: str
    token_digest: str = dataclass_field(repr=False)
    client_id_hash: str = ""
    runtime_boot_id: str = ""
    deadline_epoch: float = 0.0
    binding_source: str = ""

    def safe_projection(self) -> dict[str, Any]:
        return {
            "session_ref": self.session_ref,
            "client_id_hash": self.client_id_hash,
            "runtime_boot_id": self.runtime_boot_id,
            "deadline_epoch": self.deadline_epoch,
            "binding_source": self.binding_source,
        }


@dataclass(frozen=True)
class _Grant:
    runtime_boot_id: str
    client_instance_id: str
    client_id_hash: str
    scopes: tuple[str, ...]
    issued_at_epoch: float
    expires_at_epoch: float
    nonce_digest: str


@dataclass(frozen=True)
class _SessionBinding:
    session_ref: str
    token_digest: str = dataclass_field(repr=False)
    client_id_hash: str = ""
    runtime_boot_id: str = ""
    deadline_epoch: float = 0.0
    binding_source: str = ""


def _bounded_id(value: Any, field_name: str, *, maximum: int = 256) -> str:
    if type(value) is not str:
        raise ValueError(f"{field_name} must be a string")
    normalized = value.strip()
    if (
        not normalized
        or len(normalized) > maximum
        or _CONTROL_CHARACTER.search(normalized)
    ):
        raise ValueError(f"{field_name} must be a bounded non-empty identifier")
    return normalized


def _random_urlsafe(byte_count: int) -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(byte_count)).rstrip(b"=").decode("ascii")


def _token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("ascii", errors="ignore")).hexdigest()


def _client_hash(client_instance_id: str) -> str:
    return hashlib.sha256(client_instance_id.encode("utf-8")).hexdigest()


def _binding_source(origin: str) -> str:
    candidate = str(origin or "").strip().casefold()
    if candidate in PACKAGED_ORIGINS:
        return "packaged_desktop"
    if candidate in DEVELOPMENT_ORIGINS:
        return "development_web"
    return "loopback_web"


def _is_loopback(host: str) -> bool:
    value = str(host or "").strip()
    if not value:
        return False
    try:
        return ipaddress.ip_address(value.split("%", 1)[0]).is_loopback
    except ValueError:
        return value.casefold() == "localhost"


class RuntimeAccessAuthority:
    """Issue and validate bounded capabilities without persisting bearer values."""

    def __init__(
        self,
        runtime_boot_id: str,
        *,
        now: Callable[[], float] | None = None,
        allow_development_origins: bool = False,
        max_active: int = 128,
        max_ttl_seconds: int = 300,
        token_factory: Callable[[], str] | None = None,
        nonce_factory: Callable[[], str] | None = None,
        session_ref_factory: Callable[[], str] | None = None,
    ) -> None:
        self._runtime_boot_id = _bounded_id(runtime_boot_id, "runtime_boot_id")
        if type(max_active) is not int or max_active < 1 or max_active > 4096:
            raise ValueError("max_active must be between 1 and 4096")
        if type(max_ttl_seconds) is not int or max_ttl_seconds < 1 or max_ttl_seconds > 3600:
            raise ValueError("max_ttl_seconds must be between 1 and 3600")
        self._now = now or time.time
        self._allow_development_origins = bool(allow_development_origins)
        self._max_active = max_active
        self._max_ttl_seconds = max_ttl_seconds
        self._token_factory = token_factory or (lambda: _random_urlsafe(32))
        self._nonce_factory = nonce_factory or (lambda: _random_urlsafe(16))
        self._session_ref_factory = session_ref_factory or (lambda: _random_urlsafe(18))
        self._grants: OrderedDict[str, _Grant] = OrderedDict()
        self._sessions: OrderedDict[str, _SessionBinding] = OrderedDict()
        self._reason_counts: Counter[str] = Counter()
        self._audit = deque(maxlen=64)
        self._lock = threading.RLock()

    @property
    def runtime_boot_id(self) -> str:
        with self._lock:
            return self._runtime_boot_id

    def issue(
        self,
        client_instance_id: str,
        scopes: Iterable[str],
        *,
        ttl_seconds: int = 60,
    ) -> IssuedRuntimeCapability:
        client_id = _bounded_id(
            client_instance_id,
            "client_instance_id",
            maximum=128,
        )
        normalized_scopes = self._normalize_scopes(scopes)
        if (
            type(ttl_seconds) is not int
            or ttl_seconds < 1
            or ttl_seconds > self._max_ttl_seconds
        ):
            raise ValueError(
                f"ttl_seconds must be between 1 and {self._max_ttl_seconds}"
            )
        issued_at = self._clock()
        expires_at = issued_at + ttl_seconds
        token = self._token_factory()
        nonce = self._nonce_factory()
        if not _TOKEN_VALUE.fullmatch(token) or len(token) < 43:
            raise RuntimeError("token factory did not provide 256-bit equivalent entropy")
        if not _TOKEN_VALUE.fullmatch(nonce):
            raise RuntimeError("nonce factory returned an invalid value")
        digest = _token_digest(token)
        grant = _Grant(
            runtime_boot_id=self.runtime_boot_id,
            client_instance_id=client_id,
            client_id_hash=_client_hash(client_id),
            scopes=normalized_scopes,
            issued_at_epoch=issued_at,
            expires_at_epoch=expires_at,
            nonce_digest=_token_digest(nonce),
        )
        with self._lock:
            self._purge_expired_locked(issued_at)
            if any(
                hmac.compare_digest(digest, stored_digest)
                for stored_digest in self._grants
            ):
                raise RuntimeError("token factory produced a duplicate capability")
            self._grants[digest] = grant
            while len(self._grants) > self._max_active:
                self._grants.popitem(last=False)
            self._record_locked("capability_issued", normalized_scopes[0], grant)
        return IssuedRuntimeCapability(
            token=token,
            runtime_boot_id=grant.runtime_boot_id,
            client_instance_id=client_id,
            scopes=normalized_scopes,
            issued_at_epoch=issued_at,
            expires_at_epoch=expires_at,
            nonce=nonce,
        )

    def validate(
        self,
        token: str,
        *,
        scope: str,
        origin: str,
        peer_host: str,
    ) -> RuntimeAccessDecision:
        requested_scope = str(scope or "").strip()
        if requested_scope not in RUNTIME_ACCESS_SCOPES:
            raise ValueError("scope is not a supported runtime access scope")
        if not _is_loopback(peer_host):
            return self._deny("peer_not_loopback", requested_scope, close_code=4403)
        if not self._origin_allowed(origin):
            return self._deny("origin_denied", requested_scope, close_code=4403)
        presented = str(token or "")
        if not _TOKEN_VALUE.fullmatch(presented):
            reason = "missing_capability" if not presented else "capability_invalid"
            return self._deny(reason, requested_scope, close_code=4401)

        now = self._clock()
        digest = _token_digest(presented)
        with self._lock:
            matched_digest = next(
                (
                    stored_digest
                    for stored_digest in self._grants
                    if hmac.compare_digest(digest, stored_digest)
                ),
                None,
            )
            grant = (
                self._grants.get(matched_digest)
                if matched_digest is not None
                else None
            )
            if grant is None:
                return self._deny_locked(
                    "capability_invalid",
                    requested_scope,
                    close_code=4401,
                )
            if grant.expires_at_epoch <= now:
                self._grants.pop(matched_digest, None)
                return self._deny_locked(
                    "capability_expired",
                    requested_scope,
                    close_code=4401,
                    grant=grant,
                )
            if grant.runtime_boot_id != self._runtime_boot_id:
                self._grants.pop(matched_digest, None)
                return self._deny_locked(
                    "boot_mismatch",
                    requested_scope,
                    close_code=4401,
                    grant=grant,
                )
            if not self._grant_has_scope(grant, requested_scope):
                return self._deny_locked(
                    "scope_denied",
                    requested_scope,
                    close_code=4403,
                    grant=grant,
                )
            self._record_locked("capability_accepted", requested_scope, grant)
            return RuntimeAccessDecision(
                allowed=True,
                reason_code="authorized",
                close_code=0,
                scope=requested_scope,
                client_id_hash=grant.client_id_hash,
                expires_in_seconds=max(0.0, grant.expires_at_epoch - now),
                nonce_digest=grant.nonce_digest,
                principal=RuntimeAccessPrincipal(
                    runtime_boot_id=grant.runtime_boot_id,
                    client_id_hash=grant.client_id_hash,
                    scopes=grant.scopes,
                    issued_at_epoch=grant.issued_at_epoch,
                    expires_at_epoch=grant.expires_at_epoch,
                    binding_source=_binding_source(origin),
                ),
            )

    def open_session(
        self,
        token: str,
        *,
        scope: str,
        origin: str,
        peer_host: str,
    ) -> tuple[RuntimeAccessDecision, RuntimeAccessSession | None]:
        """Validate a bearer once and replace it with a process-local handle."""

        decision = self.validate(
            token,
            scope=scope,
            origin=origin,
            peer_host=peer_host,
        )
        if not decision.allowed or decision.principal is None:
            return decision, None
        digest = _token_digest(str(token or ""))
        session_ref = self._session_ref_factory()
        if not _TOKEN_VALUE.fullmatch(session_ref):
            raise RuntimeError("session reference factory returned an invalid value")
        principal = decision.principal
        binding = _SessionBinding(
            session_ref=session_ref,
            token_digest=digest,
            client_id_hash=principal.client_id_hash,
            runtime_boot_id=principal.runtime_boot_id,
            deadline_epoch=principal.expires_at_epoch,
            binding_source=principal.binding_source,
        )
        with self._lock:
            if session_ref in self._sessions:
                raise RuntimeError("session reference factory produced a duplicate")
            self._sessions[session_ref] = binding
            while len(self._sessions) > self._max_active * 2:
                self._sessions.popitem(last=False)
        return decision, RuntimeAccessSession(
            session_ref=binding.session_ref,
            token_digest=binding.token_digest,
            client_id_hash=binding.client_id_hash,
            runtime_boot_id=binding.runtime_boot_id,
            deadline_epoch=binding.deadline_epoch,
            binding_source=binding.binding_source,
        )

    def validate_session(
        self,
        session: RuntimeAccessSession,
        *,
        required_scopes: Iterable[str],
    ) -> RuntimeAccessDecision:
        """Revalidate current boot, revocation, expiry and exact command scopes."""

        scopes = self._normalize_scopes(required_scopes)
        primary_scope = scopes[0]
        if not isinstance(session, RuntimeAccessSession):
            return self._deny("session_invalid", primary_scope, close_code=4401)
        now = self._clock()
        with self._lock:
            if session.runtime_boot_id != self._runtime_boot_id:
                self._sessions.pop(session.session_ref, None)
                return self._deny_locked(
                    "boot_mismatch",
                    primary_scope,
                    close_code=4401,
                )
            if session.deadline_epoch <= now:
                self._sessions.pop(session.session_ref, None)
                self._grants.pop(session.token_digest, None)
                return self._deny_locked(
                    "capability_expired",
                    primary_scope,
                    close_code=4401,
                )
            binding = self._sessions.get(session.session_ref)
            if binding is None or not self._session_matches_binding(session, binding):
                return self._deny_locked(
                    "session_invalid",
                    primary_scope,
                    close_code=4401,
                )
            if binding.deadline_epoch <= now:
                self._sessions.pop(binding.session_ref, None)
                self._grants.pop(binding.token_digest, None)
                return self._deny_locked(
                    "capability_expired",
                    primary_scope,
                    close_code=4401,
                )
            grant = self._grants.get(binding.token_digest)
            if grant is None:
                return self._deny_locked(
                    "capability_revoked",
                    primary_scope,
                    close_code=4401,
                )
            if (
                grant.runtime_boot_id != binding.runtime_boot_id
                or grant.client_id_hash != binding.client_id_hash
            ):
                return self._deny_locked(
                    "session_binding_mismatch",
                    primary_scope,
                    close_code=4401,
                    grant=grant,
                )
            denied_scope = next(
                (scope_name for scope_name in scopes if not self._grant_has_scope(grant, scope_name)),
                None,
            )
            if denied_scope is not None:
                return self._deny_locked(
                    "scope_denied",
                    denied_scope,
                    close_code=4403,
                    grant=grant,
                )
            self._record_locked("session_command_accepted", primary_scope, grant)
            return RuntimeAccessDecision(
                allowed=True,
                reason_code="authorized",
                close_code=0,
                scope=primary_scope,
                client_id_hash=grant.client_id_hash,
                expires_in_seconds=max(0.0, grant.expires_at_epoch - now),
                nonce_digest=grant.nonce_digest,
                principal=RuntimeAccessPrincipal(
                    runtime_boot_id=grant.runtime_boot_id,
                    client_id_hash=grant.client_id_hash,
                    scopes=grant.scopes,
                    issued_at_epoch=grant.issued_at_epoch,
                    expires_at_epoch=grant.expires_at_epoch,
                    binding_source=binding.binding_source,
                ),
            )

    def revoke_client(self, client_instance_id: str) -> int:
        client_id = _bounded_id(
            client_instance_id,
            "client_instance_id",
            maximum=128,
        )
        with self._lock:
            matches = [
                digest
                for digest, grant in self._grants.items()
                if grant.client_instance_id == client_id
            ]
            for digest in matches:
                self._grants.pop(digest, None)
            return len(matches)

    def rotate_boot(self, runtime_boot_id: str) -> None:
        next_boot = _bounded_id(runtime_boot_id, "runtime_boot_id")
        with self._lock:
            self._runtime_boot_id = next_boot
            self._grants.clear()
            self._reason_counts["boot_rotated"] += 1

    def diagnostics(self) -> dict[str, Any]:
        now = self._clock()
        with self._lock:
            self._purge_expired_locked(now)
            return {
                "schema_version": 1,
                "runtime_boot_id_hash": _client_hash(self._runtime_boot_id),
                "active_capabilities": len(self._grants),
                "active_sessions": len(self._sessions),
                "reason_counts": dict(sorted(self._reason_counts.items())),
                "recent_audit": [dict(item) for item in self._audit],
            }

    def _normalize_scopes(self, scopes: Iterable[str]) -> tuple[str, ...]:
        if isinstance(scopes, (str, bytes)):
            raise ValueError("scopes must be an iterable of scope names")
        try:
            normalized = tuple(sorted({str(scope).strip() for scope in scopes}))
        except TypeError as exc:
            raise ValueError("scopes must be iterable") from exc
        if not normalized or any(scope not in RUNTIME_ACCESS_SCOPES for scope in normalized):
            raise ValueError("scope is not supported")
        return normalized

    @staticmethod
    def _session_matches_binding(
        session: RuntimeAccessSession,
        binding: _SessionBinding,
    ) -> bool:
        return (
            hmac.compare_digest(session.session_ref, binding.session_ref)
            and hmac.compare_digest(session.token_digest, binding.token_digest)
            and hmac.compare_digest(session.client_id_hash, binding.client_id_hash)
            and hmac.compare_digest(session.runtime_boot_id, binding.runtime_boot_id)
            and session.deadline_epoch == binding.deadline_epoch
            and session.binding_source == binding.binding_source
        )

    @staticmethod
    def _grant_has_scope(grant: _Grant, required_scope: str) -> bool:
        if required_scope in grant.scopes:
            return True
        # `conversation` was the sole pre-L6 conversation capability. Keep it
        # as an explicit compatibility grant, never as a prefix or wildcard.
        return (
            required_scope in {"conversation.read", "conversation.write"}
            and "conversation" in grant.scopes
        )

    def _clock(self) -> float:
        value = self._now()
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise RuntimeError("runtime access clock must return a finite number")
        result = float(value)
        if not math.isfinite(result):
            raise RuntimeError("runtime access clock must return a finite number")
        return result

    def _origin_allowed(self, origin: str) -> bool:
        candidate = str(origin or "").strip().casefold()
        if candidate in PACKAGED_ORIGINS:
            return True
        return self._allow_development_origins and candidate in DEVELOPMENT_ORIGINS

    def _purge_expired_locked(self, now: float) -> None:
        expired = [
            digest
            for digest, grant in self._grants.items()
            if grant.expires_at_epoch <= now
        ]
        for digest in expired:
            self._grants.pop(digest, None)
        expired_sessions = [
            session_ref
            for session_ref, binding in self._sessions.items()
            if binding.deadline_epoch <= now
        ]
        for session_ref in expired_sessions:
            self._sessions.pop(session_ref, None)

    def _deny(
        self,
        reason_code: str,
        scope: str,
        *,
        close_code: int,
    ) -> RuntimeAccessDecision:
        with self._lock:
            return self._deny_locked(reason_code, scope, close_code=close_code)

    def _deny_locked(
        self,
        reason_code: str,
        scope: str,
        *,
        close_code: int,
        grant: _Grant | None = None,
    ) -> RuntimeAccessDecision:
        self._record_locked(reason_code, scope, grant)
        return RuntimeAccessDecision(
            allowed=False,
            reason_code=reason_code,
            close_code=close_code,
            scope=scope,
            client_id_hash=grant.client_id_hash if grant is not None else "",
        )

    def _record_locked(
        self,
        reason_code: str,
        scope: str,
        grant: _Grant | None,
    ) -> None:
        self._reason_counts[reason_code] += 1
        self._audit.append(
            {
                "reason_code": reason_code,
                "scope": scope,
                "client_id_hash": grant.client_id_hash if grant is not None else "",
            }
        )


def capability_from_websocket_protocols(value: str) -> str:
    protocols = [item.strip() for item in str(value or "").split(",") if item.strip()]
    if _BASE_PROTOCOL not in protocols:
        return ""
    candidates = [
        item.removeprefix(_TOKEN_PROTOCOL_PREFIX)
        for item in protocols
        if item.startswith(_TOKEN_PROTOCOL_PREFIX)
    ]
    if len(candidates) != 1 or _TOKEN_VALUE.fullmatch(candidates[0]) is None:
        return ""
    return candidates[0]


def _header(headers: Mapping[str, Any] | Any, name: str) -> str:
    getter = getattr(headers, "get", None)
    if callable(getter):
        value = getter(name)
        if value is None:
            value = getter(name.casefold())
        return str(value or "")
    return ""


def create_websocket_authorizer(
    authority: RuntimeAccessAuthority,
) -> Callable[[Any, str], Any]:
    async def authorize(websocket: Any, scope: str) -> bool:
        headers = getattr(websocket, "headers", {})
        token = capability_from_websocket_protocols(
            _header(headers, "sec-websocket-protocol")
        )
        client = getattr(websocket, "client", None)
        handshake_scope = "conversation.read" if scope == "conversation" else scope
        decision, session = authority.open_session(
            token,
            scope=handshake_scope,
            origin=_header(headers, "origin"),
            peer_host=str(getattr(client, "host", "") or ""),
        )
        if decision.allowed and session is not None:
            socket_scope = getattr(websocket, "scope", None)
            if isinstance(socket_scope, dict):
                socket_scope["javis.runtime_access"] = {
                    "scope": scope,
                    "client_id_hash": decision.client_id_hash,
                    "nonce_digest": decision.nonce_digest,
                    "session_ref": session.session_ref,
                    "deadline_monotonic": (
                        asyncio.get_running_loop().time()
                        + decision.expires_in_seconds
                    ),
                }
                socket_scope["javis.runtime_session"] = session
                socket_scope["javis.runtime_principal"] = decision.principal
            return True
        await websocket.close(
            code=decision.close_code,
            reason=decision.reason_code,
        )
        return False

    return authorize


def websocket_access_context(websocket: Any) -> dict[str, Any] | None:
    socket_scope = getattr(websocket, "scope", None)
    if not isinstance(socket_scope, Mapping):
        return None
    value = socket_scope.get("javis.runtime_access")
    return dict(value) if isinstance(value, Mapping) else None


def websocket_runtime_principal(websocket: Any) -> RuntimeAccessPrincipal | None:
    socket_scope = getattr(websocket, "scope", None)
    if not isinstance(socket_scope, Mapping):
        return None
    value = socket_scope.get("javis.runtime_principal")
    return value if isinstance(value, RuntimeAccessPrincipal) else None


def websocket_runtime_session(websocket: Any) -> RuntimeAccessSession | None:
    socket_scope = getattr(websocket, "scope", None)
    if not isinstance(socket_scope, Mapping):
        return None
    value = socket_scope.get("javis.runtime_session")
    return value if isinstance(value, RuntimeAccessSession) else None


def validate_websocket_command(
    authority: RuntimeAccessAuthority,
    websocket: Any,
    command_type: str,
    *,
    registry: Any = None,
) -> RuntimeAccessDecision:
    """Resolve the central WS policy and revalidate the bound session."""

    if registry is None:
        from core.action.route_policy import DEFAULT_ROUTE_POLICY_REGISTRY

        registry = DEFAULT_ROUTE_POLICY_REGISTRY
    try:
        policy = registry.websocket_policy(command_type)
    except (KeyError, TypeError, ValueError):
        return authority._deny(
            "route_policy_missing",
            "conversation.read",
            close_code=4403,
        )
    session = websocket_runtime_session(websocket)
    if session is None:
        return authority._deny(
            "session_invalid",
            policy.required_scopes[0],
            close_code=4401,
        )
    return authority.validate_session(
        session,
        required_scopes=policy.required_scopes,
    )


def create_websocket_command_authorizer(
    authority: RuntimeAccessAuthority,
    *,
    registry: Any = None,
) -> Callable[[Any, str], Any]:
    async def authorize(websocket: Any, command_type: str) -> bool:
        decision = validate_websocket_command(
            authority,
            websocket,
            command_type,
            registry=registry,
        )
        if decision.allowed:
            socket_scope = getattr(websocket, "scope", None)
            if isinstance(socket_scope, dict):
                socket_scope["javis.runtime_principal"] = decision.principal
            return True
        await websocket.close(code=decision.close_code, reason=decision.reason_code)
        return False

    return authorize


def websocket_access_remaining(websocket: Any) -> float | None:
    context = websocket_access_context(websocket)
    if context is None:
        return None
    try:
        deadline = float(context["deadline_monotonic"])
    except (KeyError, TypeError, ValueError):
        return 0.0
    return max(0.0, deadline - asyncio.get_running_loop().time())


def create_http_authorizer(
    authority: RuntimeAccessAuthority,
) -> Callable[[Any, str], RuntimeAccessDecision]:
    def authorize(request: Any, scope: str) -> RuntimeAccessDecision:
        headers = getattr(request, "headers", {})
        client = getattr(request, "client", None)
        return authority.validate(
            _header(headers, "x-javis-runtime-capability"),
            scope=scope,
            origin=_header(headers, "origin"),
            peer_host=str(getattr(client, "host", "") or ""),
        )

    return authorize


__all__ = [
    "ACTION_RUNTIME_ACCESS_SCOPES",
    "DEVELOPMENT_ORIGINS",
    "ENVIRONMENT_RUNTIME_ACCESS_SCOPES",
    "IssuedRuntimeCapability",
    "MEMORY_RUNTIME_ACCESS_SCOPES",
    "PACKAGED_ORIGINS",
    "RUNTIME_ACCESS_SCOPES",
    "RuntimeAccessAuthority",
    "RuntimeAccessDecision",
    "RuntimeAccessPrincipal",
    "RuntimeAccessSession",
    "capability_from_websocket_protocols",
    "create_http_authorizer",
    "create_websocket_authorizer",
    "create_websocket_command_authorizer",
    "validate_websocket_command",
    "websocket_access_context",
    "websocket_access_remaining",
    "websocket_runtime_principal",
    "websocket_runtime_session",
]
