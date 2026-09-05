from __future__ import annotations

import ast
import re
from collections import deque
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]


def _source(relative: str) -> str:
    # utf-8-sig accepts normal UTF-8 and strips the BOM retained by a few
    # legacy production modules before parsing their syntax tree.
    return (ROOT / relative).read_text(encoding="utf-8-sig")


def _tree(relative: str) -> ast.Module:
    return ast.parse(_source(relative), filename=relative)


def _module_name(relative: str) -> str:
    path = Path(relative)
    parts = list(path.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _resolve_from(relative: str, node: ast.ImportFrom) -> str:
    if node.level == 0:
        return node.module or ""
    current = _module_name(relative).split(".")
    if Path(relative).name != "__init__.py":
        current.pop()
    keep = len(current) - (node.level - 1)
    base = current[: max(0, keep)]
    if node.module:
        base.extend(node.module.split("."))
    return ".".join(base)


def _imports(relative: str, tree: ast.AST | None = None) -> set[str]:
    parsed = tree or _tree(relative)
    result: set[str] = set()
    for node in ast.walk(parsed):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            result.add(_resolve_from(relative, node))
    return result


def _dotted(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return None if base is None else f"{base}.{node.attr}"
    return None


def _definition(tree: ast.AST, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    matches = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
    ]
    assert len(matches) == 1, f"expected exactly one definition of {name}, got {len(matches)}"
    return matches[0]


def _production_python_files() -> Iterable[str]:
    for directory in ("core", "gateway", "knowledge", "memory"):
        for path in sorted((ROOT / directory).rglob("*.py")):
            yield path.relative_to(ROOT).as_posix()
    # tools/ contains a vendored Python/Rust toolchain. Only its repository-owned
    # top-level modules are production sources for this release boundary.
    for directory in ("tools", "utils"):
        for path in sorted((ROOT / directory).glob("*.py")):
            yield path.relative_to(ROOT).as_posix()
    yield "main.py"


def _local_module_file(module: str) -> str | None:
    file_path = ROOT.joinpath(*module.split(".")).with_suffix(".py")
    if file_path.is_file():
        return file_path.relative_to(ROOT).as_posix()
    package_path = ROOT.joinpath(*module.split("."), "__init__.py")
    if package_path.is_file():
        return package_path.relative_to(ROOT).as_posix()
    return None


def test_memory_service_is_the_only_concrete_memory_store_owner() -> None:
    """Scan production AST, not repository text, for concrete MemoryStore ownership."""

    importers: set[str] = set()
    constructor_calls: list[tuple[str, int]] = []
    for relative in _production_python_files():
        tree = _tree(relative)
        class_aliases: set[str] = set()
        module_aliases: dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = _resolve_from(relative, node)
                for alias in node.names:
                    if module == "core.life.memory.store" and alias.name == "MemoryStore":
                        class_aliases.add(alias.asname or alias.name)
                        importers.add(relative)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "core.life.memory.store":
                        module_aliases[alias.asname or "core"] = alias.name
                        importers.add(relative)

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            called = _dotted(node.func)
            concrete = called in class_aliases or called == "core.life.memory.store.MemoryStore"
            concrete = concrete or any(
                called == f"{alias}.MemoryStore" and module == "core.life.memory.store"
                for alias, module in module_aliases.items()
            )
            if concrete:
                constructor_calls.append((relative, node.lineno))

    assert importers == {"core/life/memory/service.py"}
    assert all(relative == "core/life/memory/service.py" for relative, _ in constructor_calls)

    service = _tree("core/life/memory/service.py")
    writer_main = _definition(service, "_writer_main")
    factory_calls = [
        node
        for node in ast.walk(writer_main)
        if isinstance(node, ast.Call) and _dotted(node.func) == "self._store_factory"
    ]
    assert len(factory_calls) == 1
    assert {keyword.arg for keyword in factory_calls[0].keywords} >= {
        "writer_token",
        "writer_thread_id",
    }

    start = _definition(service, "start")
    writer_threads = [
        node
        for node in ast.walk(start)
        if isinstance(node, ast.Call) and _dotted(node.func) == "threading.Thread"
    ]
    assert len(writer_threads) == 1
    keywords = {keyword.arg: keyword.value for keyword in writer_threads[0].keywords}
    assert _dotted(keywords["target"]) == "self._writer_main"
    assert isinstance(keywords["name"], ast.Constant)
    assert keywords["name"].value == "javis-memory-writer"


def test_formal_agent_import_graph_has_no_legacy_controller_or_indexer() -> None:
    """Follow only local core/gateway imports reachable from formal runtime entrypoints."""

    forbidden = {"memory.controller", "memory.indexer"}
    queue = deque(
        [
            "core.agent",
            "core.runtime",
            "core.prompt_builder",
            "core.conversation_hub",
            "gateway.conversation_ws",
        ]
    )
    visited: set[str] = set()
    violations: list[tuple[str, str]] = []
    while queue:
        module = queue.popleft()
        if module in visited:
            continue
        visited.add(module)
        relative = _local_module_file(module)
        assert relative is not None, module
        for imported in _imports(relative):
            if any(imported == item or imported.startswith(item + ".") for item in forbidden):
                violations.append((module, imported))
            if imported.startswith(("core.", "gateway.")) and imported not in visited:
                if _local_module_file(imported) is not None:
                    queue.append(imported)
    assert not violations, violations


def test_authorization_boundary_imports_no_relationship_or_user_model() -> None:
    """Authority derives from capabilities and bindings, never social/model state modules."""

    boundary = (
        "core/runtime_access.py",
        "core/life/memory/access.py",
        "core/life/memory/api.py",
    )
    violations: list[tuple[str, str]] = []
    for relative in boundary:
        tree = _tree(relative)
        for module in _imports(relative, tree):
            normalized = module.casefold().replace("-", "_")
            if "relationship" in normalized or "user_model" in normalized:
                violations.append((relative, module))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    normalized = alias.name.casefold().replace("-", "_")
                    if "relationship" in normalized or "user_model" in normalized:
                        violations.append((relative, alias.name))
    assert not violations, violations


def test_every_l2_fts_match_ranks_only_precomputed_visible_ids() -> None:
    """All L2 MATCH statements must consume an ACL-filtered temporary visible-ID set."""

    relative = "core/life/memory/store.py"
    tree = _tree(relative)
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent

    match_nodes = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and re.search(r"\bmemory_fts\s+MATCH\b", node.value, flags=re.IGNORECASE)
    ]
    assert match_nodes, "release scan found no L2 FTS MATCH statement"
    for node in match_nodes:
        owner = parents.get(node)
        while owner is not None and not isinstance(owner, (ast.FunctionDef, ast.AsyncFunctionDef)):
            owner = parents.get(owner)
        assert isinstance(owner, ast.FunctionDef)
        assert owner.name == "search_items"
        assert "visible_memory_ids" in node.value

    search = _definition(tree, "search_items")
    literals = "\n".join(
        node.value
        for node in ast.walk(search)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    )
    for required in (
        "CREATE TEMP TABLE visible_memory_ids",
        "INSERT INTO visible_memory_ids",
        "owner_subject_id",
        "memory_acl",
        "a.subject_id",
        "a.permission = 'read'",
    ):
        assert required in literals

    execute_lines: dict[str, list[int]] = {"visible_sql": [], "rank_sql": []}
    for node in ast.walk(search):
        if not isinstance(node, ast.Call) or _dotted(node.func) != "db.execute" or not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Name) and first.id in execute_lines:
            execute_lines[first.id].append(node.lineno)
    assert len(execute_lines["visible_sql"]) == 1
    assert len(execute_lines["rank_sql"]) == 1
    assert execute_lines["visible_sql"][0] < execute_lines["rank_sql"][0]


def test_memory_api_authenticates_before_parsing_and_builds_server_authority() -> None:
    """Mutation routes authorize first and pass server-built AccessContext contracts."""

    tree = _tree("core/life/memory/api.py")
    for function_name in ("propose_shared", "shared_transition", "correct", "forget"):
        function = _definition(tree, function_name)
        calls = [node for node in ast.walk(function) if isinstance(node, ast.Call)]
        authorization = [node.lineno for node in calls if _dotted(node.func) == "context_for"]
        body_reads = [node.lineno for node in calls if _dotted(node.func) == "_read_json_body"]
        assert len(authorization) == len(body_reads) == 1
        assert authorization[0] < body_reads[0]

        assignments = {
            target.id: node.value
            for node in function.body
            if isinstance(node, ast.Assign)
            for target in node.targets
            if isinstance(target, ast.Name)
        }
        command_mappings: list[tuple[ast.Call, ast.Dict]] = []
        for call in calls:
            if _dotted(call.func) != "_contract" or len(call.args) < 2:
                continue
            mapping = call.args[1]
            if isinstance(mapping, ast.Name):
                mapping = assignments.get(mapping.id)
            if not isinstance(mapping, ast.Dict):
                continue
            keys = [key.value if isinstance(key, ast.Constant) else None for key in mapping.keys]
            if "access_context" in keys:
                command_mappings.append((call, mapping))

        assert len(command_mappings) == 1
        _, mapping = command_mappings[0]
        access_index = next(
            index
            for index, key in enumerate(mapping.keys)
            if isinstance(key, ast.Constant) and key.value == "access_context"
        )
        server_context = mapping.values[access_index]
        assert isinstance(server_context, ast.Call)
        assert _dotted(server_context.func) == "context.to_dict"
        assert body_reads[0] < server_context.lineno

    strict_body = _definition(tree, "_strict_body")
    assert any(
        isinstance(node, ast.Call) and _dotted(node.func) == "_reject_authority_fields"
        for node in ast.walk(strict_body)
    )


def test_episode_insert_is_reachable_only_from_candidate_projection() -> None:
    """The service has no failed/cancelled episode insertion branch."""

    tree = _tree("core/life/memory/service.py")
    reconcile = _definition(tree, "_reconcile_terminal_page")
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(reconcile):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent
    inserts = [
        node
        for node in ast.walk(reconcile)
        if isinstance(node, ast.Call) and _dotted(node.func) == "store.project_episode"
    ]
    assert len(inserts) == 1
    ancestors: list[ast.AST] = []
    current: ast.AST | None = inserts[0]
    while current in parents:
        current = parents[current]
        ancestors.append(current)
    guards = [ast.unparse(node.test) for node in ancestors if isinstance(node, ast.If)]
    assert "decision.candidate is not None" in guards


def test_deletion_six_path_fixture_is_present_and_exercises_each_path() -> None:
    """Pin the focused DB/FTS/cache/prompt/restart/reindex deletion fixture."""

    tree = _tree("tests/test_memory_deletion.py")
    fixture = _definition(
        tree,
        "test_derived_only_deletion_covers_db_fts_cache_prompt_restart_reindex_and_replay",
    )
    literals = "\n".join(
        node.value
        for node in ast.walk(fixture)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    )
    for table in ("memory_items", "memory_fts", "memory_fts_rebuilds"):
        assert table in literals
    calls = [_dotted(node.func) for node in ast.walk(fixture) if isinstance(node, ast.Call)]
    assert calls.count("_recall") >= 3
    assert "service.register_prompt_invalidator" in calls
    assert "service.shutdown" in calls
    assert "reopened.reconcile_once" in calls
    attributes = {
        _dotted(node) for node in ast.walk(fixture) if isinstance(node, ast.Attribute)
    }
    assert "agent._cached_prompt" in attributes


def test_formal_runtime_has_zero_legacy_write_calls() -> None:
    """Limit the scan to runtime execution files; archive modules remain importable."""

    formal_files = (
        "core/agent.py",
        "core/runtime.py",
        "core/conversation_hub.py",
        "gateway/conversation_ws.py",
    )
    legacy_writers = {"learn_fact", "record_experience", "learn_style"}
    violations: list[tuple[str, int, str]] = []
    for relative in formal_files:
        for node in ast.walk(_tree(relative)):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in legacy_writers:
                    violations.append((relative, node.lineno, node.func.attr))
    assert not violations, violations

    agent = _tree("core/agent.py")
    for hook_name in ("_auto_learn_from_actions", "_after_learn"):
        hook = _definition(agent, hook_name)
        statements = [
            node
            for node in hook.body
            if not (
                isinstance(node, ast.Expr)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            )
        ]
        assert len(statements) == 1 and isinstance(statements[0], ast.Return)

    runtime = _tree("core/runtime.py")
    create_runtime = _definition(runtime, "create_runtime")
    brain_calls = [
        node
        for node in ast.walk(create_runtime)
        if isinstance(node, ast.Call) and _dotted(node.func) == "Brain"
    ]
    assert len(brain_calls) == 1
    keywords = {keyword.arg: keyword.value for keyword in brain_calls[0].keywords}
    assert isinstance(keywords.get("read_only"), ast.Constant)
    assert keywords["read_only"].value is True
    assert any(
        isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "learner" for target in node.targets)
        and isinstance(node.value, ast.Constant)
        and node.value.value is None
        for node in ast.walk(create_runtime)
    )


def test_d_drive_acceptance_document_never_prefills_success() -> None:
    document = _source("docs/verification/JAVIS_L2_D_DRIVE_ACCEPTANCE.md")
    assert "Status: NOT EXECUTED" in document
    assert "PASS" not in document
    for gate in (
        "Cross-day and restart recall",
        "Source citation",
        "Shared explicit confirmation",
        "Guest privacy",
        "Model switch continuity",
        "SQLite failure degradation",
        "D-drive install",
        "D-drive upgrade",
        "D-drive uninstall data retention",
    ):
        assert f"| {gate} | NOT EXECUTED |" in document
