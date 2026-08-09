"""Run the three production conversation watchdog phases over real loopback WS."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import shutil
import socket
import sys
import tempfile
import time
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import FastAPI, WebSocket
import uvicorn

from core.agent_runs import AgentRunStore
from core.conversation_hub import ConversationHub
from core.conversation_store import ConversationStore
from gateway.conversation_stall_harness import ConversationStallHarness
from gateway.conversation_ws import ConversationWebSocketGateway


class _ForbiddenAgent:
    async def chat(self, *_args, **_kwargs):
        raise AssertionError("stall harness called the real agent")
        yield  # pragma: no cover - keeps this an async generator


def _enabled_stall_harness() -> ConversationStallHarness:
    previous = {
        name: os.environ.get(name)
        for name in ("JAVIS_TEST_MODE", "JAVIS_STALL_HARNESS")
    }
    try:
        os.environ["JAVIS_TEST_MODE"] = "1"
        os.environ["JAVIS_STALL_HARNESS"] = "1"
        harness = ConversationStallHarness.from_environment()
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
    if not harness.enabled:
        raise RuntimeError("stall harness did not pass both test environment gates")
    return harness


def _loopback_listener() -> tuple[socket.socket, int]:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    listener.setblocking(False)
    return listener, int(listener.getsockname()[1])


async def _wait_server(server: uvicorn.Server, *, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not server.started:
        if time.monotonic() >= deadline:
            raise TimeoutError("loopback watchdog server did not start")
        await asyncio.sleep(0.02)


async def _wait_clean(hub: ConversationHub, *, timeout: float = 3.0) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        stats = hub.stats()
        if stats["active_requests"] == 0 and stats["subscribers"] == 0:
            return stats
        if time.monotonic() >= deadline:
            raise AssertionError(f"watchdog server did not clean request state: {stats}")
        await asyncio.sleep(0.025)


async def _run_node(
    node: Path,
    origin: str,
    time_scale: float,
) -> dict:
    command = [
        str(node),
        "--experimental-strip-types",
        str(ROOT / "scripts" / "verify_conversation_watchdogs.ts"),
        "--origin",
        origin,
        "--time-scale",
        str(time_scale),
    ]
    process = await asyncio.create_subprocess_exec(
        *command,
        cwd=str(ROOT),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        creationflags=(
            getattr(__import__("subprocess"), "CREATE_NO_WINDOW", 0)
            if os.name == "nt"
            else 0
        ),
    )
    stdout, stderr = await process.communicate()
    output = stdout.decode("utf-8", errors="replace").strip()
    errors = stderr.decode("utf-8", errors="replace").strip()
    if process.returncode != 0:
        raise RuntimeError(errors or output or f"Node exited {process.returncode}")
    try:
        return json.loads(output.splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as error:
        raise RuntimeError(f"invalid Node watchdog report: {output[-1000:]}") from error


async def _verify(args) -> dict:
    work_dir = Path(args.work_dir).resolve()
    node = Path(args.node).resolve()
    if not work_dir.is_dir():
        raise FileNotFoundError(f"work directory does not exist: {work_dir}")
    if not node.is_file():
        raise FileNotFoundError(f"Node runtime does not exist: {node}")

    runtime_dir = Path(tempfile.mkdtemp(prefix="javis-watchdog-", dir=work_dir)).resolve()
    runtime_dir.relative_to(work_dir)
    run_store = AgentRunStore(runtime_dir / "runs.sqlite3")
    conversation_store = ConversationStore(runtime_dir / "conversations.sqlite3")
    hub = ConversationHub(conversation_store, run_store)
    runtime = SimpleNamespace(
        agent=_ForbiddenAgent(),
        conversation_store=conversation_store,
        conversation_hub=hub,
        registry=SimpleNamespace(count=0),
        llm=SimpleNamespace(model="stall-harness"),
    )
    gateway = ConversationWebSocketGateway(
        runtime,
        stall_harness=_enabled_stall_harness(),
    )
    app = FastAPI()

    @app.websocket("/ws")
    async def websocket_endpoint(ws: WebSocket):
        await gateway.serve(ws)

    listener, port = _loopback_listener()
    server = uvicorn.Server(uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
        lifespan="off",
    ))
    server_task = asyncio.create_task(server.serve(sockets=[listener]))
    runs: list[dict] = []
    final_stats: dict = {}
    try:
        await _wait_server(server)
        origin = f"http://127.0.0.1:{port}"
        for _ in range(args.passes):
            run = await _run_node(node, origin, args.time_scale)
            final_stats = await _wait_clean(hub)
            runs.append(run)
        return {
            "qualification": (
                "release-timing" if args.time_scale == 1 else "source-regression"
            ),
            "time_scale": args.time_scale,
            "passes": args.passes,
            "active_requests": final_stats.get("active_requests", -1),
            "subscribers": final_stats.get("subscribers", -1),
            "runs": runs,
        }
    finally:
        server.should_exit = True
        try:
            await asyncio.wait_for(server_task, timeout=5)
        except asyncio.TimeoutError:
            server_task.cancel()
            await asyncio.gather(server_task, return_exceptions=True)
        listener.close()
        await hub.shutdown()
        run_store.close()
        runtime_dir.relative_to(work_dir)
        shutil.rmtree(runtime_dir, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify Javis accepted/first-response/overall watchdogs",
    )
    parser.add_argument("--node", required=True, type=Path)
    parser.add_argument("--work-dir", required=True, type=Path)
    parser.add_argument("--time-scale", type=float, default=1.0)
    parser.add_argument("--passes", type=int, default=1)
    args = parser.parse_args()
    if not 0 < args.time_scale <= 1:
        parser.error("--time-scale must be greater than zero and at most one")
    if not 1 <= args.passes <= 5:
        parser.error("--passes must be between 1 and 5")
    try:
        report = asyncio.run(_verify(args))
    except Exception as error:
        print(str(error), file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
