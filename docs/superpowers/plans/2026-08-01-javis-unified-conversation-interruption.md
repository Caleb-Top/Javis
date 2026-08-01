# Javis Unified Conversation and Interruption Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Live, Code, voice, and Pet share one durable conversation and one interruptible execution stream, with a detailed Code activity timeline and condensed Live/Pet status.

**Architecture:** `JarvisRuntime` will own a SQLite `ConversationStore` and asynchronous `ConversationHub`. The hub owns request tasks, cooperative cancellation, ordered replayable events, subscribers, and durable `AgentRunRecorder` lifecycles. Native Live and embedded Code remain separate views, but attach to the same backend session ID and consume the same request-scoped event stream.

**Tech Stack:** Python 3.11, asyncio, FastAPI WebSocket, SQLite/FTS5, pytest/unittest, TypeScript, DOM JavaScript, Vite, Tauri 2, pnpm, Cargo, PowerShell.

## Global Constraints

- Work, caches, tests, build output, and packaging stay on `G:`.
- Preserve the five-system blueprint, memory, models, training results, user data, and historical design records.
- Preserve the current Live orb and Pet appearance; behavior wiring may change, visual renderer and skin assets may not.
- Do not expose model chain-of-thought or raw hidden reasoning.
- External skills remain `candidate/untested` until separately evaluated and promoted.
- All execution remains behind `ToolRegistry`, `ToolGuard`, permission, approval, timeout, audit, and rollback boundaries.
- One mutating request runs at a time for the personal-assistant runtime; replacement requests cancel and then continue at a safe boundary.
- Client `recent_cards` are compatibility input, not authoritative memory.
- Each behavior change follows RED, GREEN, regression verification, and a focused commit.

---

## File Structure

**New backend units**

- `core/cancellation.py`: cooperative cancellation token and cancel-aware await helper.
- `core/conversation_store.py`: durable sessions, messages, ordered events, and idempotency keys.
- `core/conversation_hub.py`: active request ownership, event broadcast/replay, cancellation, approval routing, and run recording.
- `core/conversation_protocol.py`: new/legacy wire message normalization and compatibility serialization.
- `gateway/conversation_ws.py`: FastAPI WebSocket receive/send orchestration with no agent logic in `main.py`.

**New app/web units**

- `app/src/conversation/conversationSession.ts`: stable native session identity.
- `app/src/conversation/ConversationEventReducer.ts`: request-scoped UI state and stale-event rejection.
- `web/js/conversationActivity.js`: pure activity reducer plus DOM timeline renderer exposed as `globalThis.JavisConversationActivity`.

Existing files are modified only where their current responsibility requires it.

---

### Task 1: Durable Conversation Store

**Files:**
- Create: `core/conversation_store.py`
- Create: `tests/test_conversation_store.py`

**Interfaces:**
- Produces: `ConversationStore(path)`.
- Produces: `ensure_session(session_id: str) -> dict`.
- Produces: `append_message(session_id, request_id, role, content, status="complete") -> dict`.
- Produces: `history(session_id, limit=80, include_interrupted=False) -> list[dict]`.
- Produces: `append_event(session_id, request_id, event_type, payload) -> dict`.
- Produces: `events_after(session_id, sequence=0, limit=500) -> list[dict]`.
- Produces: `claim_idempotency_key(session_id, key, request_id) -> tuple[str, bool]`.

- [ ] **Step 1: Write persistence, ordering, history, and idempotency tests**

```python
def test_events_are_monotonic_and_survive_reopen(tmp_path):
    store = ConversationStore(tmp_path / "conversations.sqlite3")
    first = store.append_event("s1", "r1", "request.accepted", {"text": "hello"})
    second = store.append_event("s1", "r1", "activity.planning", {"detail": "plan"})
    assert [first["sequence"], second["sequence"]] == [1, 2]
    assert [event["type"] for event in store.events_after("s1", 1)] == ["activity.planning"]

def test_interrupted_assistant_output_is_not_normal_context(tmp_path):
    store = ConversationStore(tmp_path / "conversations.sqlite3")
    store.append_message("s1", "r1", "user", "old request")
    store.append_message("s1", "r1", "assistant", "partial", status="interrupted")
    store.append_message("s1", "r2", "assistant", "complete answer")
    assert [item["content"] for item in store.history("s1")] == ["old request", "complete answer"]
```

- [ ] **Step 2: Run the new tests and confirm RED**

Run:

```powershell
. .\scripts\javis_dev_env.ps1
G:\Javis\venv\Scripts\python.exe -m pytest tests\test_conversation_store.py -q
```

Expected: import failure for `core.conversation_store`.

- [ ] **Step 3: Implement schema version 1 and transactional sequence allocation**

Use SQLite tables `conversations`, `conversation_messages`,
`conversation_events`, `request_keys`, and `conversation_meta`. Allocate each
session sequence inside `BEGIN IMMEDIATE`; validate roles, message states, event
types, limits, and non-empty IDs before writing.

```python
class ConversationStore:
    def append_event(self, session_id, request_id, event_type, payload):
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            sequence = db.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM conversation_events WHERE session_id=?",
                (session_id,),
            ).fetchone()[0]
            # Insert event and update conversation timestamp in this transaction.
            db.commit()
        return self._event_dict(session_id, request_id, sequence, event_type, payload)
```

- [ ] **Step 4: Verify focused and existing persistence tests**

Run:

```powershell
G:\Javis\venv\Scripts\python.exe -m pytest tests\test_conversation_store.py tests\test_agent_runs.py tests\test_p0_runtime.py -q
```

Expected: all selected tests pass, including the existing `SessionEventStore`
coverage inside `test_p0_runtime.py`.

- [ ] **Step 5: Commit the store**

```powershell
git add core/conversation_store.py tests/test_conversation_store.py
git commit -m "feat: add durable conversation store"
```

---

### Task 2: Conversation Hub and Cooperative Cancellation

**Files:**
- Create: `core/cancellation.py`
- Create: `core/conversation_hub.py`
- Create: `tests/test_conversation_hub.py`
- Modify: `core/agent_run_recorder.py`

**Interfaces:**
- Consumes: `ConversationStore` from Task 1.
- Consumes: `AgentRunStore` and `AgentRunRecorder`.
- Produces: `CancellationToken.cancel(reason)`, `cancelled`, `reason`, `checkpoint()`, and `race(awaitable)`.
- Produces: `ConversationRequest(session_id, request_id, text, interaction_mode, idempotency_key)`.
- Produces: `ConversationHub.attach(session_id, after_sequence=0) -> ConversationSubscription`.
- Produces: `ConversationHub.submit(request, runner) -> dict`.
- Produces: `ConversationHub.cancel(session_id, request_id="", reason="user interrupt") -> bool`.
- Produces: `ConversationHub.confirm(session_id, request_id, approval_id, confirmed) -> bool`.
- Produces: `ConversationHub.wait_for_terminal(request_id, timeout=5) -> dict`.
- Runner signature: `Callable[[ConversationRequest, CancellationToken], AsyncIterator[dict]]`.

- [ ] **Step 1: Write asynchronous hub tests**

```python
class ConversationHubTests(unittest.IsolatedAsyncioTestCase):
    async def test_replacement_cancels_old_request_and_rejects_late_delta(self):
        release = asyncio.Event()

        async def runner(request, token):
            yield {"type": "thinking", "content": "understanding"}
            if request.request_id == "r1":
                await release.wait()
                await token.checkpoint()
            yield {"type": "text_delta", "text": request.text}
            yield {"type": "done"}

        await hub.submit(ConversationRequest("s1", "r1", "old", "live", "k1"), runner)
        await hub.submit(ConversationRequest("s1", "r2", "new", "code", "k2"), runner)
        release.set()
        await hub.wait_for_terminal("r2")
        events = store.events_after("s1")
        assert any(e["type"] == "request.cancelled" and e["request_id"] == "r1" for e in events)
        assert not any(e["type"] == "response.delta" and e["request_id"] == "r1" for e in events)
```

Also test subscriber broadcast, replay after a cursor, duplicate idempotency keys,
explicit stop without replacement, approval ID matching, and interrupted assistant
message persistence.

- [ ] **Step 2: Run the hub tests and confirm RED**

```powershell
G:\Javis\venv\Scripts\python.exe -m pytest tests\test_conversation_hub.py -q
```

Expected: imports for `CancellationToken` and `ConversationHub` fail.

- [ ] **Step 3: Implement cancellation and hub lifecycle**

```python
class CancellationToken:
    def __init__(self):
        self._event = asyncio.Event()
        self.reason = ""

    def cancel(self, reason="cancelled"):
        self.reason = str(reason)
        self._event.set()

    async def checkpoint(self):
        if self._event.is_set():
            raise RequestCancelled(self.reason)

    async def race(self, awaitable):
        operation = asyncio.create_task(awaitable)
        cancellation = asyncio.create_task(self._event.wait())
        done, pending = await asyncio.wait(
            {operation, cancellation}, return_when=asyncio.FIRST_COMPLETED
        )
        if cancellation in done:
            operation.cancel()
            raise RequestCancelled(self.reason)
        cancellation.cancel()
        return await operation
```

The hub must persist before broadcast, copy each event for subscribers, cap replay
at 500 events, and serialize replacement transitions with one asyncio lock. It
must not force-cancel an unknown active tool; token checkpoints create the safe
boundary.

- [ ] **Step 4: Make `AgentRunRecorder.cancel()` preserve cancelled semantics**

Add tests asserting active steps, tasks, approvals, and runs all become
`cancelled`, not `failed`, and that a later `done` cannot overwrite cancellation.

- [ ] **Step 5: Verify hub, store, and run graph regressions**

```powershell
G:\Javis\venv\Scripts\python.exe -m pytest tests\test_conversation_store.py tests\test_conversation_hub.py tests\test_agent_runs.py tests\test_runtime_agent_fusion.py -q
```

- [ ] **Step 6: Commit the execution core**

```powershell
git add core/cancellation.py core/conversation_hub.py core/agent_run_recorder.py tests/test_conversation_hub.py
git commit -m "feat: add interruptible conversation hub"
```

---

### Task 3: Agent Cancellation, Shared History, and Activity Events

**Files:**
- Modify: `core/agent.py`
- Create: `tests/test_agent_interruptible_conversation.py`
- Modify: `tests/test_p0_runtime.py`

**Interfaces:**
- Consumes: `CancellationToken` from Task 2.
- Changes: `Agent.chat(..., cancellation: CancellationToken | None = None)`.
- Produces: `_conversation_history(cards, limit=40) -> list[dict]`.
- Produces existing-compatible `activity` messages with fields `activity` and `detail`.

- [ ] **Step 1: Write tests for shared history and cancellation points**

```python
async def test_conversation_cards_replace_unrelated_agent_window():
    agent.state.messages = [{"role": "user", "content": "other session"}]
    cards = [
        {"role": "user", "text": "my name is Eric"},
        {"role": "assistant", "text": "I will remember that"},
    ]
    messages = agent._conversation_history(cards)
    assert messages[0]["content"] == "my name is Eric"
    assert all(item["content"] != "other session" for item in messages)

async def test_cancel_during_llm_wait_emits_cancelled_without_answer():
    token = CancellationToken()
    stream = agent.chat("long task", session_id="s1", cancellation=token)
    assert (await anext(stream))["type"] in {"thinking", "activity"}
    token.cancel("replacement request")
    remaining = [message async for message in stream]
    assert not any(message.get("type") == "text_delta" for message in remaining)
```

Add a fake slow tool test proving cancellation requested during a tool waits for
the tool result, then stops before another tool starts.

- [ ] **Step 2: Run tests and confirm RED**

```powershell
G:\Javis\venv\Scripts\python.exe -m pytest tests\test_agent_interruptible_conversation.py -q
```

Expected: `Agent.chat` rejects the `cancellation` keyword or history assertion fails.

- [ ] **Step 3: Normalize authoritative history**

```python
def _conversation_history(self, cards, limit=40):
    history = []
    for card in (cards or [])[-limit:]:
        role = str(card.get("role", ""))
        content = str(card.get("content") or card.get("text") or "").strip()
        if role in {"user", "assistant"} and content and card.get("status") != "interrupted":
            history.append({"role": role, "content": content[:4000]})
    return history
```

Use this history for quick and full paths when supplied. Keep `state.messages` as
the legacy fallback only.

- [ ] **Step 4: Add cancellation checks and useful activity events**

Emit only summaries:

```python
yield {"type": "activity", "activity": "understanding", "detail": "Understanding the request"}
yield {"type": "activity", "activity": "planning", "detail": f"Planning step {self.state.step}"}
yield {"type": "activity", "activity": "verifying", "detail": "Verifying the result"}
```

Use `await cancellation.race(...)` around LLM requests, `checkpoint()` before each
tool, and `checkpoint()` after each tool. On tool failure, emit
`activity.fallback` and continue with the structured failure in model context.
Never emit `resp.reasoning_content` to clients.

- [ ] **Step 5: Verify agent, memory recall, tool, and guardrail regressions**

```powershell
G:\Javis\venv\Scripts\python.exe -m pytest tests\test_agent_interruptible_conversation.py tests\test_tool_catalog.py tests\test_p0_runtime.py -q
```

- [ ] **Step 6: Commit agent behavior**

```powershell
git add core/agent.py tests/test_agent_interruptible_conversation.py tests/test_p0_runtime.py
git commit -m "feat: make agent conversations interruptible"
```

---

### Task 4: Runtime Ownership and WebSocket Conversation Gateway

**Files:**
- Create: `core/conversation_protocol.py`
- Create: `gateway/conversation_ws.py`
- Modify: `core/runtime.py`
- Modify: `main.py`
- Create: `tests/test_conversation_protocol.py`
- Create: `tests/test_conversation_gateway.py`
- Modify: `tests/test_runtime_agent_fusion.py`

**Interfaces:**
- Consumes: store, hub, cancellation, and agent stream from Tasks 1-3.
- Produces: `normalize_client_message(message, default_session="") -> ClientCommand`.
- Produces: `legacy_wire_events(event) -> list[dict]`.
- Produces: `ConversationWebSocketGateway(runtime, transcribe, local_action_resolver).serve(ws)`.
- Adds: `JarvisRuntime.conversation_store` and `JarvisRuntime.conversation_hub`.

- [ ] **Step 1: Write new and legacy protocol tests**

```python
def test_legacy_message_normalizes_to_conversation_command():
    command = normalize_client_message({
        "type": "message",
        "payload": {"text": "hello", "session_id": "s1", "request_id": "r1"},
    })
    assert command.type == "conversation.message"
    assert command.session_id == "s1"
    assert command.request_id == "r1"

def test_activity_event_has_legacy_thinking_projection():
    projected = legacy_wire_events({
        "type": "activity.planning", "session_id": "s1", "request_id": "r1",
        "sequence": 3, "payload": {"detail": "Planning"},
    })
    assert projected[0]["type"] == "thinking"
```

Reject missing/oversized IDs, cross-session approvals, unknown commands, and
non-dict payloads with structured errors.

- [ ] **Step 2: Write a fake-WebSocket integration test**

The fake socket provides `accept`, `receive_text`, `send_json`, and a disconnect
sentinel. Test attach, submit, two subscribers receiving the same ordered events,
cancel, confirm matching, and legacy ping.

- [ ] **Step 3: Run protocol/gateway/runtime tests and confirm RED**

```powershell
G:\Javis\venv\Scripts\python.exe -m pytest tests\test_conversation_protocol.py tests\test_conversation_gateway.py tests\test_runtime_agent_fusion.py -q
```

- [ ] **Step 4: Implement protocol adapters and gateway sender/receiver tasks**

```python
class ConversationWebSocketGateway:
    async def serve(self, ws):
        await ws.accept()
        subscription = None
        sender = None
        try:
            while True:
                command = normalize_client_message(json.loads(await ws.receive_text()))
                # Attach or dispatch without awaiting the request execution task.
        finally:
            if subscription:
                await subscription.close()
            if sender:
                sender.cancel()
```

The sender broadcasts canonical events and compatibility projections appropriate
to the client's negotiated protocol. The receiver never blocks on the agent run,
so cancel and approval messages remain responsive.

- [ ] **Step 5: Assemble the runtime and replace the nested `_agent_loop`**

Create the store below `root/data/conversations/conversations.sqlite3`; inject the
shared event bus and run store; expose store/hub statistics from
`get_runtime_status()`. Keep `/ws` and existing API routes stable. `main.py` should
construct the gateway and delegate the endpoint, not contain another agent loop.

- [ ] **Step 6: Verify backend APIs and real startup smoke**

Start uvicorn hidden on port 18080, attach two WebSocket clients to one session,
submit a deterministic fake/test-mode request, interrupt it, and assert ordered
terminal events plus a cancelled durable run. Also recheck `/api/status`,
`/api/runtime/status`, catalogs, and agent-runs.

- [ ] **Step 7: Commit runtime and protocol integration**

```powershell
git add core/conversation_protocol.py gateway/conversation_ws.py core/runtime.py main.py tests/test_conversation_protocol.py tests/test_conversation_gateway.py tests/test_runtime_agent_fusion.py
git commit -m "feat: route websocket sessions through conversation hub"
```

---

### Task 5: Native App Session and Request-Scoped Event State

**Files:**
- Create: `app/src/conversation/conversationSession.ts`
- Create: `app/src/conversation/ConversationEventReducer.ts`
- Modify: `app/src/app/AppPreferences.ts`
- Modify: `app/src/bridge/backendClient.ts`
- Modify: `app/src/code/CodeSurface.ts`
- Modify: `app/src/live/LiveCaption.ts`
- Modify: `app/src/main.ts`
- Create: `app/tests/conversationSession.test.ts`
- Create: `app/tests/conversationEventReducer.test.ts`
- Modify: `app/tests/backendEndpoints.test.ts`
- Modify: `app/tests/surfaceStatus.test.ts`

**Interfaces:**
- Produces: `getOrCreateConversationId(storage, randomId) -> string`.
- Produces: `ConversationEventReducer.accept(event) -> ConversationUiSnapshot`.
- Changes `createBackendClient` options to require `sessionId`.
- Adds `BackendClient.cancel(reason?)`, `activeRequestId()`, and `sessionId()`.
- Changes `openCodeSurface(sessionId)` so the iframe attaches to the same session.

- [ ] **Step 1: Write stable-ID and stale-event reducer tests**

```typescript
test("the conversation id survives Live and Code surface changes", () => {
  const storage = new MapStorage();
  const first = getOrCreateConversationId(storage, () => "session-1");
  const second = getOrCreateConversationId(storage, () => "session-2");
  assert.equal(first, "session-1");
  assert.equal(second, "session-1");
});

test("late deltas from an interrupted request are ignored", () => {
  const reducer = new ConversationEventReducer();
  reducer.accept(event("request.accepted", "r2", 10));
  const snapshot = reducer.accept(event("response.delta", "r1", 11, { text: "late" }));
  assert.equal(snapshot.response, "");
  assert.equal(snapshot.activeRequestId, "r2");
});
```

- [ ] **Step 2: Run app tests and confirm RED**

```powershell
Set-Location app
. ..\scripts\javis_dev_env.ps1
pnpm.cmd test
```

- [ ] **Step 3: Implement session persistence and canonical backend commands**

Add string preference helpers without changing existing boolean helpers. On
WebSocket open, send `conversation.attach` with the last sequence. Send
`conversation.message`, `conversation.voice`, `conversation.cancel`, and
`conversation.confirm` with the stable session and active request IDs.

- [ ] **Step 4: Map canonical events to shared Live state**

The reducer owns `activeRequestId`, last sequence, response buffer, terminal
state, and concise activity. `LiveCaption` starts only on `request.accepted`,
appends only matching deltas, and seals or clears on terminal events. Persist the
last sequence only after reducer acceptance.

- [ ] **Step 5: Pass the same session into Code immediately**

```typescript
const url = new URL("/", resolveBackendEndpoints().http);
url.searchParams.set("app_embed", "1");
url.searchParams.set("session_id", sessionId);
```

Opening Code remains synchronous; changing the iframe URL must not invoke the
model or create a new conversation.

- [ ] **Step 6: Verify all app tests and production build**

```powershell
pnpm.cmd test
pnpm.cmd build
```

- [ ] **Step 7: Commit native shared-session wiring**

```powershell
git add app/src/conversation app/src/app/AppPreferences.ts app/src/bridge/backendClient.ts app/src/code/CodeSurface.ts app/src/live/LiveCaption.ts app/src/main.ts app/tests
git commit -m "feat: share one conversation across native surfaces"
```

---

### Task 6: Code Activity Timeline and Interrupt Controls

**Files:**
- Create: `web/js/conversationActivity.js`
- Modify: `web/index.html`
- Modify: `web/js/app.js`
- Modify: `web/css/style.css`
- Create: `app/tests/codeActivityTimeline.test.ts`
- Modify: `tests/test_app_packaging.py`

**Interfaces:**
- Produces global `JavisConversationActivity.reduce(state, event)`.
- Produces global `JavisConversationActivity.mount(root, options)`.
- Timeline methods: `begin`, `accept`, `complete`, `cancel`, `fail`, and `dispose`.

- [ ] **Step 1: Write reducer and source-contract tests**

Load `conversationActivity.js` with `node:vm` and test pure reduction for
understanding, planning, tools, approval, fallback, verifying, completion, and
cancellation. Assert the renderer never displays `reasoning_content`, raw secrets,
or tool payload JSON by default.

```typescript
test("completed activity remains collapsible instead of disappearing", () => {
  let state = api.reduce(api.initialState("r1"), event("activity.planning", 2));
  state = api.reduce(state, event("request.completed", 3));
  assert.equal(state.terminal, "completed");
  assert.equal(state.collapsed, true);
  assert.equal(state.items.length, 1);
});
```

- [ ] **Step 2: Run app tests and confirm RED**

```powershell
Set-Location app
pnpm.cmd test
```

- [ ] **Step 3: Implement durable activity cards**

Replace `tsCreate/tsStep/tsHide` with the new controller. A card contains a compact
animated summary row, elapsed time, disclosure button, ordered timeline, and stop
button. Tool details remain collapsed. Completion collapses the card; it is not
removed on the first response delta.

- [ ] **Step 4: Keep Code input active and add cancel-and-replace**

Remove `if (isProcessing) return`. A non-empty input while active sends
`conversation.message`; the hub handles replacement. The dedicated stop icon
sends `conversation.cancel` and does not clear typed text. Ensure quick actions
use the same path.

- [ ] **Step 5: Attach Code to the native session**

Read `session_id` from the iframe query string, attach on WebSocket open, and use
it for message, upload, confirmation, memory save/load, and activity replay.
Preserve standalone browser behavior by generating a local session only when the
query parameter is absent.

- [ ] **Step 6: Verify Web Code responsiveness and responsive layout**

At desktop and collapsed-sidebar widths, confirm the activity card and composer
remain aligned, the stop icon is at least 36 logical pixels, long tool names wrap,
and timeline expansion does not cover the composer.

- [ ] **Step 7: Commit Code guidance UI**

```powershell
git add web/js/conversationActivity.js web/index.html web/js/app.js web/css/style.css app/tests/codeActivityTimeline.test.ts tests/test_app_packaging.py
git commit -m "feat: add Code activity guidance and interruption"
```

---

### Task 7: Voice Barge-In, TTS Stop, and Verified Fallback Status

**Files:**
- Modify: `app/src/live/VoiceCapture.ts`
- Modify: `app/src/main.ts`
- Modify: `web/js/app.js`
- Modify: `app/src/state/runtimeStateTypes.ts`
- Modify: `app/src/live/SurfaceStatus.ts`
- Create: `app/tests/voiceBargeIn.test.ts`
- Modify: `app/tests/surfaceStatus.test.ts`
- Modify: `tests/test_agent_interruptible_conversation.py`

**Interfaces:**
- Adds `VoiceCaptureOptions.onBargeIn() -> void | Promise<void>`.
- Adds Web `stopAudioPlayback()`.
- Maps `activity.fallback` to a short, non-success status.

- [x] **Step 1: Write voice interruption and fallback tests**

Assert that starting a new capture calls `onBargeIn` before the native capture
endpoint, Web `playAudio` is stopped before a replacement voice message, and a
failed tool never produces a completed/saved claim unless a later tool succeeds.

- [x] **Step 2: Run focused tests and confirm RED**

```powershell
Set-Location app
pnpm.cmd test
Set-Location ..
G:\Javis\venv\Scripts\python.exe -m pytest tests\test_agent_interruptible_conversation.py -q
```

- [x] **Step 3: Implement barge-in ordering**

The order is: stop TTS, send cancel for active request, enter listening state,
capture, transcribe, then submit the new voice request in the same session. Do not
queue raw microphone audio for reconnect.

- [x] **Step 4: Implement concise fallback states**

Code receives the full structured fallback activity. Live/Pet display a bounded
message such as `Trying a local fallback`; they return to failure if no verified
tool succeeds. No client invents success from assistant wording.

- [ ] **Step 5: Verify voice diagnostics are unchanged**

Run app tests and backend voice integration tests. In the Tauri app, manually
verify microphone permission, one spoken interruption, TTS stop, and continuous
follow-up. Record hardware and Windows permission results separately from
automated status.

- [x] **Step 6: Commit barge-in behavior**

```powershell
git add app/src/live/VoiceCapture.ts app/src/main.ts web/js/app.js app/src/state/runtimeStateTypes.ts app/src/live/SurfaceStatus.ts app/tests/voiceBargeIn.test.ts app/tests/surfaceStatus.test.ts tests/test_agent_interruptible_conversation.py
git commit -m "feat: add voice barge-in and fallback status"
```

---

### Task 8: Native Continuous Audio, Noise Suppression, and Streaming STT

**Files:**
- Create: `app/src-tauri/src/audio/` native frame engine modules
- Modify: `app/src-tauri/src/main.rs`
- Modify: `app/src-tauri/Cargo.toml`
- Create: `voice/streaming_pipeline.py`
- Modify: `voice/stt.py`
- Modify: `app/src/live/VoiceCapture.ts`
- Modify: `app/src/main.ts`
- Create: `tests/test_streaming_voice_pipeline.py`
- Create: `app/tests/continuousVoiceCapture.test.ts`

**Interfaces:**
- Native commands: `audio_stream_start`, `audio_stream_stop`,
  `audio_stream_status`, and `audio_playback_stop`.
- Native events: `javis://audio-level`, `javis://speech-start`,
  `javis://transcript-partial`, `javis://transcript-final`, and
  `javis://audio-error`.
- Python `StreamingVoicePipeline.push_frame(frame) -> VoicePipelineEvents`.

- [x] **Step 1: Write failing frame, endpoint, and interruption tests**

Cover bounded ring-buffer behavior, speech pre-roll, VAD hysteresis, partial
replacement, final transcript order, continuous second turns, queue overload,
and a speech-start event stopping TTS before request cancellation.

- [x] **Step 2: Add deterministic noisy-speech fixtures**

Generate or keep small licensed fixtures for clean speech, fan-like stationary
noise, keyboard impulses, and TTS echo. Assert intelligible speech is preserved,
silence does not create final transcripts, and stronger suppression is opt-in.

- [x] **Step 3: Implement native Windows frame capture**

Capture 48 kHz mono PCM as 20 ms frames through an isolated Python
PyAudio/PortAudio worker. Keep capture and playback references process-wide so
the microphone remains open across turns. The App does not call browser media
APIs in production Tauri mode.

- [x] **Step 4: Integrate reduced-mode AEC, NS, and AGC**

The installed dependency set does not contain WebRTC APM or RNNoise. The current
reduced mode uses synchronized adaptive playback-reference cancellation, adaptive
Wiener suppression, bounded AGC, and off/standard/strong profiles. Diagnostics
report optional backend availability separately and never claim those modules are
active.

- [x] **Step 5: Implement rolling VAD and streaming transcription**

Silero is not installed, so reduced mode uses adaptive energy/ZCR VAD with onset
hysteresis. It emits bounded partial hypotheses every 800 ms and finalizes after
900 ms, which avoids false cuts across measured Chinese SAPI pauses. Only final
segments become conversation messages; partials update Live status.

- [x] **Step 6: Wire continuous conversation and native barge-in**

Keep capture active while Javis thinks and speaks. New speech onset stops native
TTS, cancels the current request through the shared `ConversationHub`, and submits
the finalized replacement segment in the same session.

- [ ] **Step 7: Verify latency, noise, and privacy behavior**

Record first-partial, endpoint, barge-in, queue, overrun, and real-time-factor
metrics. Confirm raw audio is not persisted by default. Separate automated fixture
results from a real Windows microphone, speaker, and noisy-room hardware test.

Automated fixtures, Windows microphone streaming (48 kHz/20 ms), offline SAPI
speech, local faster-whisper recognition, queue bounds, and no-raw-audio behavior
have passed. A human noisy-room barge-in and speaker audibility pass remains a
release acceptance item and is not reported as complete.

- [x] **Step 8: Commit the native continuous voice path**

```powershell
git add app/src-tauri app/src/live/VoiceCapture.ts app/src/main.ts voice tests/test_streaming_voice_pipeline.py app/tests/continuousVoiceCapture.test.ts
git commit -m "feat: add native continuous voice pipeline"
```

---

### Task 9: Cross-Surface Integration and Global Verification

**Files:**
- Create: `scripts/verify_unified_conversation.py`
- Create: `tests/test_unified_conversation_integration.py`
- Modify: `docs/superpowers/plans/2026-08-01-javis-unified-conversation-interruption.md`
- Create: `docs/JAVIS_UNIFIED_CONVERSATION_VERIFICATION_2026-08-01.md`

**Interfaces:**
- Produces a deterministic verification script returning nonzero on session,
  ordering, cancellation, replay, persistence, or stale-delta failure.

- [x] **Step 1: Add an end-to-end test harness**

The harness starts the backend in test mode on a free G-rooted temp directory,
opens two WebSockets as Live and Code, attaches both to one session, submits from
each surface, cancels/replaces one request, reconnects after a cursor, and checks
the durable conversation and run databases.

- [x] **Step 2: Run all Python suites**

```powershell
. .\scripts\javis_dev_env.ps1
.\scripts\run_python_tests.ps1
```

Expected: zero failures across `tests`, `agent_distill/tests`, and
`ClaudeAgent_Distill/agent_distill/tests`.

- [x] **Step 3: Run frontend and native builds**

```powershell
Set-Location app
pnpm.cmd test
pnpm.cmd build
Set-Location src-tauri
cargo test --locked
cargo check --locked
```

- [ ] **Step 4: Run API, WebSocket, and packaged-sidecar smoke tests**

Verify `/api/status`, `/api/runtime/status`, tool/skill catalogs, agent runs,
conversation stats, two-surface replay, and graceful shutdown. Confirm no test
server or build process remains afterward.

Source backend API/WebSocket and shutdown checks pass. Packaged-sidecar smoke is
deferred to Task 10 because the new runtime archive has not been generated yet.

- [ ] **Step 5: Perform visual and interaction QA**

Use the native Tauri app for Live/Code/Settings/Pet transitions. Verify Code opens
immediately, displays the current Live task, remains aligned with sidebar open and
closed, allows stop and replacement input, and returns to Live without session
loss. Check desktop and compact window sizes.

Automated transition/layout tests pass and the native window compiles and starts.
The current Windows desktop helper intermittently fails transparent-window cursor
capture with `0x80070005`, so manual mouse-driven native QA remains unclaimed.

- [x] **Step 6: Prove visual and path invariants**

Compare pre-feature hashes for `LiveOrbRenderer.ts`, `LiveOrb.ts`, selected orb
icon assets, Pet skins, and Pet renderer assets. Scan active source/config/build
scripts for fixed `D:\Javis` project paths. Confirm caches, temp, Cargo target,
pnpm store, runtime staging, and release output are under `G:`.

- [x] **Step 7: Write the verification report and commit**

Report exact commands, counts, durations, hardware results, residual limitations,
upstream provenance, and rollback commits. Do not label unperformed microphone or
system-audio checks as passed.

```powershell
git add scripts/verify_unified_conversation.py tests/test_unified_conversation_integration.py docs/JAVIS_UNIFIED_CONVERSATION_VERIFICATION_2026-08-01.md docs/superpowers/plans/2026-08-01-javis-unified-conversation-interruption.md
git commit -m "test: verify unified interruptible conversations"
```

---

### Task 10: Merge, Reverify, and Build v3 Release

**Files:**
- Modify only generated release manifests under the existing release pipeline.
- Do not modify blueprint, memory, model, training, or Live visual source files.

**Interfaces:**
- Consumes all verified commits from Tasks 1-9.
- Produces merged `G:\Javis`, NSIS installer, standalone ZIP, checksum manifest,
  and installed-app test report.

- [ ] **Step 1: Audit the dirty main worktree before merge**

Compare `G:\Javis` modified files with the branch. Preserve unrelated user files
and untracked data. Commit only known migration configuration when required; do
not reset, checkout, or delete unrelated changes.

- [ ] **Step 2: Merge non-interactively into `G:\Javis`**

```powershell
git merge --no-ff codex/javis-agent-fusion-a1-a4 -m "merge: integrate Javis agent and unified conversation runtime"
```

- [ ] **Step 3: Repeat the complete verification matrix on merged main**

Repeat Python, app, Vite, Cargo, API/WebSocket, visual transition, G-path, and
process checks. A branch-only pass is not a release pass.

- [ ] **Step 4: Regenerate the complete Python runtime archive**

Use the current fused source with the existing runtime staging scripts. Verify
the archive contains the Python runtime, five-system backend, conversation schema,
external candidate skills, Web Code assets, native app assets, license/provenance,
and no source-worktree absolute paths.

- [ ] **Step 5: Build NSIS and standalone ZIP on G**

Run the existing `scripts/build_javis_app_v3.ps1` pipeline with G-rooted Cargo,
pnpm, pip, temp, and staging directories. Produce SHA-256 manifests and do not
reuse the old one-gigabyte runtime ZIP copied only for development checks.

- [ ] **Step 6: Install into a clean test directory and run the installed app**

Verify first-launch extraction and hashes, sidecar startup, Live-only initial
surface, Code open, one continuous conversation across Live/Code, cancellation,
settings, user-data persistence across restart, and uninstall preservation rules.

- [ ] **Step 7: Replace Desktop v3 only after installed-app success**

Delete or replace only the explicitly identified old Desktop v3 release artifacts.
Place the verified installer, standalone ZIP, checksum manifest, and test report
in one Desktop release folder. Do not delete the app source or build pipeline.

- [ ] **Step 8: Final release evidence**

Record final commit, artifact paths, byte sizes, SHA-256 values, test counts,
installed version, backend status, and any physical hardware checks that remain
user-dependent.

---

## Rollback Points

- Task 1 store is additive and removable without changing existing memory.
- Task 2 hub is isolated behind runtime ownership.
- Task 3 Agent signature remains backward compatible through a default `None` token.
- Task 4 retains legacy WebSocket projections.
- Task 5 native session changes can revert without deleting backend history.
- Task 6 Code timeline is one controller and one stylesheet section.
- Task 7 voice barge-in is isolated behind one callback.
- Task 8 native audio is isolated behind a frame-source and processing interface.
- Every task ends in a focused commit before the merge commit.

## Definition of Done

The feature is done only when source and installed app both demonstrate one
conversation across Live and Code, deterministic cancel-and-replace, no stale
deltas, a durable Code activity timeline, concise Live/Pet status, preserved
permissions and run audit, unchanged Live/Pet visuals, complete test/build gates,
and a fresh independently installable v3 release.
