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
from dataclasses import dataclass
from typing import Any


RUNTIME_ACCESS_SCOPES = frozenset(
    {
        "conversation",
        "diagnostics.read",
        "life.read",
        "playback",
        "voice.capture",
    }
)
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


@dataclass(frozen=True)
class _Grant:
    runtime_boot_id: str
    client_instance_id: str
    client_id_hash: str
    scopes: tuple[str, ...]
    issued_at_epoch: float
    expires_at_epoch: float
    nonce_digest: str


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
    return hashlib.sha256(client_instance_id.encode("utf-8")).hexdigest()[:16]


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
        self._grants: OrderedDict[str, _Grant] = OrderedDict()
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
            if requested_scope not in grant.scopes:
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
        decision = authority.validate(
            token,
            scope=scope,
            origin=_header(headers, "origin"),
            peer_host=str(getattr(client, "host", "") or ""),
        )
        if decision.allowed:
            socket_scope = getattr(websocket, "scope", None)
            if isinstance(socket_scope, dict):
                socket_scope["javis.runtime_access"] = {
                    "scope": decision.scope,
                    "client_id_hash": decision.client_id_hash,
                    "nonce_digest": decision.nonce_digest,
                    "deadline_monotonic": (
                        asyncio.get_running_loop().time()
                        + decision.expires_in_seconds
                    ),
                }
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
    "DEVELOPMENT_ORIGINS",
    "IssuedRuntimeCapability",
    "PACKAGED_ORIGINS",
    "RUNTIME_ACCESS_SCOPES",
    "RuntimeAccessAuthority",
    "RuntimeAccessDecision",
    "capability_from_websocket_protocols",
    "create_http_authorizer",
    "create_websocket_authorizer",
    "websocket_access_context",
    "websocket_access_remaining",
]
