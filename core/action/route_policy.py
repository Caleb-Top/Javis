"""Central, machine-auditable runtime scope policy for HTTP and WebSocket routes."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any, Iterable

from core.runtime_access import RUNTIME_ACCESS_SCOPES


MUTATING_HTTP_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_HTTP_METHODS = MUTATING_HTTP_METHODS | frozenset({"GET", "HEAD", "OPTIONS"})
_BOOTSTRAP_ROUTE = ("POST", "/api/runtime/access")


class RouteMode(str, Enum):
    READ_ONLY = "read_only"
    MUTATION = "mutation"
    BOOTSTRAP = "bootstrap"


@dataclass(frozen=True, slots=True)
class RoutePolicy:
    method: str
    path: str
    mode: RouteMode
    required_scopes: tuple[str, ...]
    executor_required: bool
    ownership_required: bool = False
    loopback_required: bool = True
    origin_required: bool = True

    def __post_init__(self) -> None:
        method = _method(self.method)
        path = _path(self.path)
        mode = self.mode if isinstance(self.mode, RouteMode) else RouteMode(self.mode)
        scopes = _scopes(self.required_scopes, allow_empty=mode is RouteMode.BOOTSTRAP)
        if mode is RouteMode.BOOTSTRAP:
            if (method, path) != _BOOTSTRAP_ROUTE:
                raise ValueError("runtime access is the only bootstrap mutation exception")
            if scopes or self.executor_required:
                raise ValueError("bootstrap policy cannot require a scope or executor")
            if not (self.ownership_required and self.loopback_required and self.origin_required):
                raise ValueError("bootstrap policy requires ownership, loopback, and origin")
        elif not scopes:
            raise ValueError("non-bootstrap routes require at least one exact scope")
        if method in MUTATING_HTTP_METHODS and mode is RouteMode.READ_ONLY:
            raise ValueError("mutating HTTP methods cannot be read-only")
        if type(self.executor_required) is not bool:
            raise TypeError("executor_required must be bool")
        for field_name in ("ownership_required", "loopback_required", "origin_required"):
            if type(getattr(self, field_name)) is not bool:
                raise TypeError(f"{field_name} must be bool")
        object.__setattr__(self, "method", method)
        object.__setattr__(self, "path", path)
        object.__setattr__(self, "mode", mode)
        object.__setattr__(self, "required_scopes", scopes)

    @property
    def key(self) -> tuple[str, str]:
        return self.method, self.path


@dataclass(frozen=True, slots=True)
class WebSocketCommandPolicy:
    command: str
    required_scopes: tuple[str, ...]
    executor_required: bool

    def __post_init__(self) -> None:
        command = str(self.command or "").strip().casefold()
        if not command or len(command) > 128 or any(char in command for char in "*\r\n\x00"):
            raise ValueError("command must be a bounded exact name")
        scopes = _scopes(self.required_scopes)
        if type(self.executor_required) is not bool:
            raise TypeError("executor_required must be bool")
        object.__setattr__(self, "command", command)
        object.__setattr__(self, "required_scopes", scopes)


@dataclass(frozen=True, slots=True)
class RouteInventoryReport:
    unknown_http_mutations: tuple[str, ...] = ()
    unknown_websocket_commands: tuple[str, ...] = ()

    @property
    def healthy(self) -> bool:
        return not self.unknown_http_mutations and not self.unknown_websocket_commands

    def safe_projection(self) -> dict[str, Any]:
        return {
            "healthy": self.healthy,
            "unknown_http_mutations": list(self.unknown_http_mutations),
            "unknown_websocket_commands": list(self.unknown_websocket_commands),
        }


class RoutePolicyInventoryError(RuntimeError):
    pass


class RoutePolicyRegistry:
    """Exact-match policy registry with FastAPI and WS inventory checks."""

    def __init__(self) -> None:
        self._http: dict[tuple[str, str], RoutePolicy] = {}
        self._websocket: dict[str, WebSocketCommandPolicy] = {}

    @property
    def http_policies(self):
        return MappingProxyType(dict(self._http))

    @property
    def websocket_policies(self):
        return MappingProxyType(dict(self._websocket))

    def register_http(self, policy: RoutePolicy) -> None:
        if not isinstance(policy, RoutePolicy):
            raise TypeError("policy must be RoutePolicy")
        if policy.key in self._http:
            raise ValueError(f"duplicate HTTP route policy: {policy.method} {policy.path}")
        if policy.path == _BOOTSTRAP_ROUTE[1] and policy.mode is not RouteMode.BOOTSTRAP:
            raise ValueError("runtime access must remain the bootstrap policy")
        if policy.path == "/api/runtime/shutdown":
            if policy.required_scopes != ("runtime.shutdown",) or not policy.ownership_required:
                raise ValueError("runtime shutdown requires exact scope and sidecar ownership")
        self._http[policy.key] = policy

    def register_websocket(self, policy: WebSocketCommandPolicy) -> None:
        if not isinstance(policy, WebSocketCommandPolicy):
            raise TypeError("policy must be WebSocketCommandPolicy")
        if policy.command in self._websocket:
            raise ValueError(f"duplicate WebSocket command policy: {policy.command}")
        self._websocket[policy.command] = policy

    def http_policy(self, method: str, path: str) -> RoutePolicy:
        return self._http[(_method(method), _path(path))]

    def websocket_policy(self, command: str) -> WebSocketCommandPolicy:
        normalized = str(command or "").strip().casefold()
        return self._websocket[normalized]

    def inventory(
        self,
        app: Any,
        *,
        websocket_commands: Iterable[str] = (),
        effectful_get_paths: Iterable[str] = (),
    ) -> RouteInventoryReport:
        marked_gets = {_path(path) for path in effectful_get_paths}
        unknown_http: set[str] = set()
        for route in tuple(getattr(app, "routes", ()) or ()):
            path = str(getattr(route, "path", "") or "")
            methods = getattr(route, "methods", ()) or ()
            for method_value in methods:
                method = str(method_value or "").strip().upper()
                if method in MUTATING_HTTP_METHODS or (method == "GET" and path in marked_gets):
                    key = (method, path)
                    if key not in self._http:
                        unknown_http.add(f"{method} {path}")
        unknown_ws = {
            str(command or "").strip().casefold()
            for command in websocket_commands
            if str(command or "").strip().casefold() not in self._websocket
        }
        return RouteInventoryReport(
            unknown_http_mutations=tuple(sorted(unknown_http)),
            unknown_websocket_commands=tuple(sorted(unknown_ws)),
        )

    def assert_inventory(self, app: Any, **kwargs: Any) -> RouteInventoryReport:
        report = self.inventory(app, **kwargs)
        if not report.healthy:
            missing = report.unknown_http_mutations + report.unknown_websocket_commands
            raise RoutePolicyInventoryError("unclassified runtime mutation: " + ", ".join(missing))
        return report

    def startup_health(self, app: Any, **kwargs: Any) -> dict[str, Any]:
        report = self.inventory(app, **kwargs)
        projection = report.safe_projection()
        projection["component"] = "route_policy_inventory"
        projection["severity"] = "ok" if report.healthy else "p0_unhealthy"
        return projection


def _method(value: Any) -> str:
    method = str(value or "").strip().upper()
    if method not in _HTTP_METHODS:
        raise ValueError("unsupported HTTP method")
    return method


def _path(value: Any) -> str:
    path = str(value or "").strip()
    if not path.startswith("/") or len(path) > 512 or any(char in path for char in "*\r\n\x00"):
        raise ValueError("path must be a bounded exact route")
    return path


def _scopes(values: Iterable[str], *, allow_empty: bool = False) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise ValueError("required_scopes must be an iterable of exact scopes")
    try:
        scopes = tuple(dict.fromkeys(str(value or "").strip() for value in values))
    except TypeError as exc:
        raise ValueError("required_scopes must be iterable") from exc
    if not scopes and not allow_empty:
        raise ValueError("required_scopes cannot be empty")
    if any(scope not in RUNTIME_ACCESS_SCOPES or "*" in scope for scope in scopes):
        raise ValueError("required scope is not in the runtime scope catalog")
    return scopes


def build_default_route_policy_registry() -> RoutePolicyRegistry:
    registry = RoutePolicyRegistry()

    def mutation(
        path: str,
        *scopes: str,
        method: str = "POST",
        executor: bool = True,
        ownership: bool = False,
    ) -> None:
        registry.register_http(
            RoutePolicy(
                method=method,
                path=path,
                mode=RouteMode.MUTATION,
                required_scopes=tuple(scopes),
                executor_required=executor,
                ownership_required=ownership,
            )
        )

    registry.register_http(
        RoutePolicy(
            method="POST",
            path="/api/runtime/access",
            mode=RouteMode.BOOTSTRAP,
            required_scopes=(),
            executor_required=False,
            ownership_required=True,
        )
    )
    mutation("/api/runtime/shutdown", "runtime.shutdown", ownership=True)

    for path in ("/api/voice/playback/speak", "/api/voice/playback/stop"):
        mutation(path, "playback")
    for path in ("/api/voice/capture/start", "/api/voice/capture/stop"):
        mutation(path, "voice.capture")
    for path in ("/api/voice/capture/probe", "/api/voice/stt/test", "/api/voice/tts/test"):
        mutation(path, "diagnostics.run", executor=False)

    for path in ("/api/skill-catalog/evaluate", "/api/skill-catalog/status"):
        mutation(path, "skills.write")
    mutation("/api/agent-runs", "intent.write", executor=False)
    mutation("/api/agent-runs/{run_id}/cancel", "conversation.cancel")
    mutation("/api/agent-runs/{run_id}/resume", "action.execute")
    mutation("/api/agent-runs/approvals/{approval_id}", "action.approve", executor=False)
    mutation("/api/diagnostics/self-test", "diagnostics.run", executor=False)

    for path in (
        "/api/perception/ingest",
        "/api/perception/yolo/detect",
        "/api/perception/image/analyze",
        "/api/perception/video/analyze",
    ):
        mutation(path, "environment.observe")
    mutation("/api/perception/screen/analyze", "environment.observe", "screen.capture")
    mutation("/api/perception/camera/analyze", "environment.observe", "camera.capture")

    mutation("/api/engine/restore", "action.recover")
    mutation("/api/logs/clear", "action.execute")
    for path in (
        "/api/memory/consolidate",
        "/api/memory/candidates/status",
        "/api/memory/apply-active",
        "/api/memory/materialize-procedural",
        "/api/memory/conversations/{sid}",
        "/api/memory/conversations/{sid}/rename",
        "/api/memory/index/rebuild",
    ):
        mutation(path, "memory.write")
    mutation("/api/memory/conversations/{sid}", "memory.write", method="DELETE")
    for path in (
        "/api/evolution/review",
        "/api/evolution/candidates/status",
        "/api/evolution/candidates/validate",
        "/api/evolution/candidates/performance",
    ):
        mutation(path, "evolution.write")
    mutation("/api/skills/activate", "skills.write")

    for path in (
        "/api/config/provider",
        "/api/config/apikey",
        "/api/config/model",
        "/api/config/models",
        "/api/config/models/remote",
        "/api/config/models/local",
        "/api/config/paths",
        "/api/config/effort",
    ):
        mutation(path, "config.write")
    for path in (
        "/api/config/models/install/detect",
        "/api/config/models/install/pause",
        "/api/config/models/install/resume",
        "/api/config/models/install/cancel",
        "/api/config/models/install/plan",
        "/api/config/models/install",
    ):
        mutation(path, "models.install")
    mutation("/api/config/permission", "permission.write")

    mutation("/api/workspace/terminal", "action.execute")
    mutation("/api/control/commands/start", "action.execute")
    mutation("/api/control/commands/{task_id}/cancel", "control.cancel")
    mutation("/api/control/rollback/restore", "action.recover")
    mutation("/api/control/root-token", "control.root.issue", executor=False)
    mutation("/api/control/fuse/trip", "control.fuse.trip")
    mutation("/api/control/fuse/reset", "control.fuse.reset")
    mutation("/api/workspace/save", "workspace.write")
    mutation("/api/workspace/project", "workspace.write")

    def websocket(command: str, *scopes: str, executor: bool = False) -> None:
        registry.register_websocket(
            WebSocketCommandPolicy(
                command=command,
                required_scopes=tuple(scopes),
                executor_required=executor,
            )
        )

    websocket("conversation.attach", "conversation.read")
    websocket("ping", "conversation.read")
    websocket("conversation.message", "conversation.write")
    websocket("voice", "conversation.write", "voice.capture")
    websocket("conversation.cancel", "conversation.cancel", executor=True)
    websocket("conversation.confirm", "action.approve")
    websocket("conversation.approval.resolve", "action.approve")
    websocket("tool", "action.execute", executor=True)
    websocket("folder_file", "workspace.write", executor=True)
    websocket("permission_change", "permission.write", executor=True)
    return registry


DEFAULT_ROUTE_POLICY_REGISTRY = build_default_route_policy_registry()


__all__ = [
    "DEFAULT_ROUTE_POLICY_REGISTRY",
    "MUTATING_HTTP_METHODS",
    "RouteInventoryReport",
    "RouteMode",
    "RoutePolicy",
    "RoutePolicyInventoryError",
    "RoutePolicyRegistry",
    "WebSocketCommandPolicy",
    "build_default_route_policy_registry",
]
