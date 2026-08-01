"""End-to-end verification for the shared Live/Code conversation runtime."""

from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
import socket
import sys
from types import SimpleNamespace
import urllib.request
import uuid

from fastapi import FastAPI, WebSocket
import uvicorn
import websockets


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.agent_runs import AgentRunStore
from core.conversation_hub import ConversationHub
from core.conversation_store import ConversationStore
from gateway.conversation_ws import ConversationWebSocketGateway


TERMINAL_TYPES = {"request.completed", "request.cancelled", "request.failed"}


class DeterministicAgent:
    async def chat(self, text: str, **kwargs):
        cancellation = kwargs["cancellation"]
        yield {
            "type": "activity",
            "activity": "understanding",
            "detail": "Understanding request",
        }
        await cancellation.checkpoint()
        if text == "slow request":
            await cancellation.race(asyncio.sleep(30))
            yield {"type": "text_delta", "text": "stale response"}
            return
        await asyncio.sleep(0.01)
        await cancellation.checkpoint()
        yield {"type": "text_delta", "text": f"reply:{text}"}
        yield {"type": "done", "success": True}


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as candidate:
        candidate.bind(("127.0.0.1", 0))
        return int(candidate.getsockname()[1])


def _check(passed: bool, detail: str) -> dict:
    return {"passed": bool(passed), "detail": str(detail)}


async def _wait_server(server: uvicorn.Server, timeout: float = 10.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not server.started:
        if asyncio.get_running_loop().time() >= deadline:
            raise TimeoutError("verification backend did not start")
        await asyncio.sleep(0.02)


async def _receive_until(socket_client, predicate, *, timeout: float = 6.0) -> list[dict]:
    events: list[dict] = []
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate(events):
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise TimeoutError(f"timed out waiting for conversation events: {events[-8:]}")
        payload = json.loads(
            await asyncio.wait_for(socket_client.recv(), timeout=remaining)
        )
        if isinstance(payload, dict):
            events.append(payload)
    return events


async def _attach(socket_client, session_id: str, after_sequence: int = 0) -> dict:
    await socket_client.send(json.dumps({
        "type": "conversation.attach",
        "payload": {
            "session_id": session_id,
            "after_sequence": after_sequence,
            "protocol_version": 2,
        },
    }))
    events = await _receive_until(
        socket_client,
        lambda rows: any(row.get("type") == "conversation.attached" for row in rows),
    )
    return events[-1]


async def _send_message(
    socket_client,
    *,
    session_id: str,
    request_id: str,
    text: str,
    interaction_mode: str,
) -> None:
    await socket_client.send(json.dumps({
        "type": "conversation.message",
        "payload": {
            "session_id": session_id,
            "request_id": request_id,
            "idempotency_key": request_id,
            "text": text,
            "interaction_mode": interaction_mode,
            "protocol_version": 2,
        },
    }))


def _sequence(events: list[dict]) -> int:
    return max(
        (int(event.get("sequence") or 0) for event in events),
        default=0,
    )


def _request_events(events: list[dict], request_id: str) -> list[dict]:
    return [event for event in events if event.get("request_id") == request_id]


def _status(port: int) -> dict:
    with urllib.request.urlopen(
        f"http://127.0.0.1:{port}/api/runtime/status",
        timeout=3,
    ) as response:
        return json.loads(response.read().decode("utf-8"))


async def run_verification(root: Path = ROOT, *, data_root: Path | None = None) -> dict:
    root = Path(root).resolve()
    if data_root is None:
        data_root = root / "tmp" / "unified-conversation-verification" / uuid.uuid4().hex
    data_root = Path(data_root).resolve()
    data_root.mkdir(parents=True, exist_ok=True)
    conversation_path = data_root / "conversations.sqlite3"
    run_path = data_root / "agent-runs.sqlite3"
    conversation_store = ConversationStore(conversation_path)
    run_store = AgentRunStore(run_path)
    hub = ConversationHub(conversation_store, run_store)
    runtime = SimpleNamespace(
        agent=DeterministicAgent(),
        conversation_store=conversation_store,
        conversation_hub=hub,
        agent_runs=run_store,
        registry=SimpleNamespace(count=9),
        llm=SimpleNamespace(model="deterministic-verifier"),
    )
    gateway = ConversationWebSocketGateway(runtime)

    @asynccontextmanager
    async def lifespan(_app):
        try:
            yield
        finally:
            await hub.shutdown()
            run_store.close()

    app = FastAPI(lifespan=lifespan)

    @app.get("/api/status")
    async def api_status():
        return {"service": "javis-verifier", "ok": True}

    @app.get("/api/runtime/status")
    async def api_runtime_status():
        return {
            "ok": True,
            "conversations": {
                **conversation_store.stats(),
                **hub.stats(),
            },
            "agent_runs": run_store.stats(),
        }

    @app.websocket("/ws")
    async def websocket_endpoint(ws: WebSocket):
        await gateway.serve(ws)

    port = _free_port()
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="error",
        access_log=False,
        lifespan="on",
    )
    server = uvicorn.Server(config)
    server.install_signal_handlers = lambda: None
    server_task = asyncio.create_task(server.serve())
    checks: dict[str, dict] = {}
    session_id = f"verify-{uuid.uuid4().hex}"
    first_request = "request-live-1"
    slow_request = "request-code-slow"
    replacement_request = "request-live-replacement"
    all_live_events: list[dict] = []
    all_code_events: list[dict] = []

    try:
        await _wait_server(server)
        uri = f"ws://127.0.0.1:{port}/ws"
        live = await websockets.connect(uri, open_timeout=5)
        code = await websockets.connect(uri, open_timeout=5)
        try:
            await _attach(live, session_id)
            await _attach(code, session_id)
            await _send_message(
                live,
                session_id=session_id,
                request_id=first_request,
                text="first turn",
                interaction_mode="live",
            )
            first_live = await _receive_until(
                live,
                lambda rows: any(
                    row.get("type") == "request.completed"
                    and row.get("request_id") == first_request
                    for row in rows
                ),
            )
            first_code = await _receive_until(
                code,
                lambda rows: any(
                    row.get("type") == "request.completed"
                    and row.get("request_id") == first_request
                    for row in rows
                ),
            )
            all_live_events.extend(first_live)
            all_code_events.extend(first_code)
            replay_cursor = _sequence(first_code)
            first_live_types = [
                event.get("type") for event in _request_events(first_live, first_request)
            ]
            first_code_types = [
                event.get("type") for event in _request_events(first_code, first_request)
            ]
            checks["two_surface_delivery"] = _check(
                first_live_types == first_code_types
                and "response.delta" in first_live_types
                and "request.completed" in first_live_types,
                f"Live={first_live_types}; Code={first_code_types}",
            )

            await code.close()
            await _send_message(
                live,
                session_id=session_id,
                request_id=slow_request,
                text="slow request",
                interaction_mode="code",
            )
            slow_started = await _receive_until(
                live,
                lambda rows: any(
                    row.get("type") == "request.accepted"
                    and row.get("request_id") == slow_request
                    for row in rows
                ),
            )
            all_live_events.extend(slow_started)
            await _send_message(
                live,
                session_id=session_id,
                request_id=replacement_request,
                text="replacement turn",
                interaction_mode="live",
            )
            replacement_events = await _receive_until(
                live,
                lambda rows: (
                    any(
                        row.get("type") == "request.cancelled"
                        and row.get("request_id") == slow_request
                        for row in rows
                    )
                    and any(
                        row.get("type") == "request.completed"
                        and row.get("request_id") == replacement_request
                        for row in rows
                    )
                ),
            )
            all_live_events.extend(replacement_events)
            slow_events = _request_events(all_live_events, slow_request)
            replacement_types = [
                event.get("type")
                for event in _request_events(all_live_events, replacement_request)
            ]
            checks["cancel_replace"] = _check(
                any(event.get("type") == "request.cancelled" for event in slow_events)
                and "request.completed" in replacement_types,
                f"cancelled={ [event.get('type') for event in slow_events] }; replacement={replacement_types}",
            )
            cancel_sequence = max(
                (
                    int(event.get("sequence") or 0)
                    for event in slow_events
                    if event.get("type") == "request.cancelled"
                ),
                default=0,
            )
            stale = [
                event
                for event in slow_events
                if event.get("type") == "response.delta"
                and int(event.get("sequence") or 0) > cancel_sequence
            ]
            checks["no_stale_delta"] = _check(
                cancel_sequence > 0 and not stale,
                f"cancel_sequence={cancel_sequence}; stale={len(stale)}",
            )

            reconnected = await websockets.connect(uri, open_timeout=5)
            try:
                await _attach(reconnected, session_id, replay_cursor)
                replay = await _receive_until(
                    reconnected,
                    lambda rows: any(
                        row.get("type") == "request.completed"
                        and row.get("request_id") == replacement_request
                        for row in rows
                    ),
                )
            finally:
                await reconnected.close()
            all_code_events.extend(replay)
            replay_types = {
                (event.get("request_id"), event.get("type"))
                for event in replay
                if isinstance(event.get("request_id"), str)
                and isinstance(event.get("type"), str)
            }
            replay_sequences = [
                int(event["sequence"])
                for event in replay
                if isinstance(event.get("sequence"), int)
            ]
            checks["cursor_replay"] = _check(
                (slow_request, "request.cancelled") in replay_types
                and (replacement_request, "request.completed") in replay_types
                and replay_sequences == sorted(set(replay_sequences))
                and all(sequence > replay_cursor for sequence in replay_sequences),
                f"cursor={replay_cursor}; replay={sorted(replay_types)}",
            )
            status = await asyncio.to_thread(_status, port)
            checks["runtime_status"] = _check(
                status.get("ok") is True
                and status.get("conversations", {}).get("sessions", 0) >= 1,
                json.dumps(status, ensure_ascii=False, sort_keys=True),
            )
        finally:
            await live.close()
            await code.close()
    finally:
        server.should_exit = True
        try:
            await asyncio.wait_for(server_task, timeout=10)
        except asyncio.TimeoutError:
            server.force_exit = True
            await asyncio.wait_for(server_task, timeout=5)

    reopened_conversations = ConversationStore(conversation_path)
    reopened_runs = AgentRunStore(run_path)
    try:
        history = reopened_conversations.history(session_id, limit=20)
        run_stats = reopened_runs.stats()
    finally:
        reopened_runs.close()
    checks["conversation_persistence"] = _check(
        [entry.get("role") for entry in history]
        == ["user", "assistant", "user", "user", "assistant"],
        json.dumps(history, ensure_ascii=False)[:1200],
    )
    checks["agent_run_persistence"] = _check(
        run_stats.get("total") == 3
        and run_stats.get("by_status", {}).get("cancelled") == 1
        and run_stats.get("by_status", {}).get("completed") == 2,
        json.dumps(run_stats, ensure_ascii=False, sort_keys=True),
    )
    checks["graceful_shutdown"] = _check(
        server_task.done() and hub.stats()["active_requests"] == 0,
        f"server_done={server_task.done()}; hub={hub.stats()}",
    )
    checks["g_rooted_data"] = _check(
        data_root.drive.upper() == root.drive.upper() == "G:",
        str(data_root),
    )
    return {
        "ok": all(check["passed"] for check in checks.values()),
        "session_id": session_id,
        "data_root": str(data_root),
        "checks": checks,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify shared Javis conversations")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = asyncio.run(run_verification(args.root, data_root=args.data_root))
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
