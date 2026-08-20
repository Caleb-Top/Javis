from __future__ import annotations

import ast
from pathlib import Path

import pytest
from fastapi import FastAPI

from core.action.route_policy import (
    DEFAULT_ROUTE_POLICY_REGISTRY,
    MUTATING_HTTP_METHODS,
    RouteMode,
    RoutePolicy,
    RoutePolicyInventoryError,
    RoutePolicyRegistry,
)


ROOT = Path(__file__).resolve().parents[1]


async def _endpoint():
    return None


def _main_mutation_app() -> FastAPI:
    tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8-sig"))
    app = FastAPI()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
                continue
            method = decorator.func.attr.upper()
            if method not in MUTATING_HTTP_METHODS or not decorator.args:
                continue
            path_node = decorator.args[0]
            if isinstance(path_node, ast.Constant) and isinstance(path_node.value, str):
                app.add_api_route(path_node.value, _endpoint, methods=[method])
    return app


def test_default_registry_has_zero_gap_against_real_main_route_decorators():
    report = DEFAULT_ROUTE_POLICY_REGISTRY.assert_inventory(_main_mutation_app())

    assert report.healthy
    assert report.unknown_http_mutations == ()
    assert len(DEFAULT_ROUTE_POLICY_REGISTRY.http_policies) >= 50


def test_inventory_fails_closed_for_a_new_unregistered_mutation():
    app = _main_mutation_app()
    app.add_api_route("/api/unregistered-effect", _endpoint, methods=["PATCH"])

    report = DEFAULT_ROUTE_POLICY_REGISTRY.inventory(app)
    assert report.healthy is False
    assert report.unknown_http_mutations == ("PATCH /api/unregistered-effect",)
    health = DEFAULT_ROUTE_POLICY_REGISTRY.startup_health(app)
    assert health["severity"] == "p0_unhealthy"
    with pytest.raises(RoutePolicyInventoryError, match="unclassified runtime mutation"):
        DEFAULT_ROUTE_POLICY_REGISTRY.assert_inventory(app)


def test_runtime_access_is_the_only_owned_bootstrap_exception():
    access = DEFAULT_ROUTE_POLICY_REGISTRY.http_policy("POST", "/api/runtime/access")
    assert access.mode is RouteMode.BOOTSTRAP
    assert access.required_scopes == ()
    assert access.executor_required is False
    assert access.ownership_required
    assert access.loopback_required
    assert access.origin_required

    with pytest.raises(ValueError, match="only bootstrap"):
        RoutePolicy(
            method="POST",
            path="/api/another-bootstrap",
            mode=RouteMode.BOOTSTRAP,
            required_scopes=(),
            executor_required=False,
            ownership_required=True,
        )


def test_shutdown_requires_both_exact_runtime_scope_and_ownership():
    shutdown = DEFAULT_ROUTE_POLICY_REGISTRY.http_policy(
        "POST", "/api/runtime/shutdown"
    )
    assert shutdown.mode is RouteMode.MUTATION
    assert shutdown.required_scopes == ("runtime.shutdown",)
    assert shutdown.ownership_required
    assert shutdown.executor_required

    registry = RoutePolicyRegistry()
    with pytest.raises(ValueError, match="shutdown requires"):
        registry.register_http(
            RoutePolicy(
                method="POST",
                path="/api/runtime/shutdown",
                mode=RouteMode.MUTATION,
                required_scopes=("runtime.shutdown",),
                executor_required=True,
                ownership_required=False,
            )
        )


def test_every_registered_mutation_uses_only_exact_catalog_scopes():
    for (method, path), policy in DEFAULT_ROUTE_POLICY_REGISTRY.http_policies.items():
        assert method in MUTATING_HTTP_METHODS
        assert policy.path == path
        assert all("*" not in scope for scope in policy.required_scopes)
        if path != "/api/runtime/access":
            assert policy.mode is RouteMode.MUTATION
            assert policy.required_scopes
