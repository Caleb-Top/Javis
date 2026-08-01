# Javis Unified Conversation and Interruption Design

**Date:** 2026-08-01  
**Status:** Approved direction (Approach A), pending written-spec review  
**Scope:** Code activity guidance, Live condensed processing state, interruption, and continuous cross-surface conversation

## 1. Decision

Javis will use one backend-owned conversation and execution coordinator for Live,
Code, voice, and Pet surfaces. A surface is a view and input channel, not an
independent agent session.

Code will show a collapsible activity timeline for each assistant turn. Live and
Pet will consume the same events but render only a short status near the orb or
pet. A new text or voice instruction will interrupt the current request and
replace it in the same conversation.

The activity timeline exposes useful execution facts, not private model
chain-of-thought. It may show understanding, planning, tools, approvals,
verification, fallback, completion, errors, and cancellation.

## 2. Existing Problems

The current application has four separate sources of conversational behavior:

- Live uses the native app `BackendClient` and a fixed `javis-app-live` session.
- Code embeds the legacy Web interface, which manages its own `currentThreadId`.
- Code rejects new input while `isProcessing` is true.
- The WebSocket agent loop can read confirmation, permission, and ping messages
  while an agent is running, but it does not accept cancellation or a replacement
  message.

Live also sends recent cards as client-owned context, but does not consistently
append assistant output to those cards. Switching surfaces can therefore change
what Javis remembers about the current conversation.

The recently integrated A1-A4 foundation solves adjacent infrastructure needs:
typed events, middleware, progressive tool discovery, governed skills, and
durable agent runs. This design builds on those systems instead of adding another
parallel state mechanism.

## 3. Goals

- One stable conversation identity across Live, Code, voice, and Pet.
- One active request per conversation with deterministic cancel-and-replace.
- Code displays concise, structured progress while a task is running.
- Live and Pet stay visually minimal and keep the current orb/pet appearance.
- Voice barge-in stops speech output immediately and interrupts active reasoning.
- Conversation history survives surface switches, reconnects, and app restarts.
- Approvals, tool calls, cancellations, and fallbacks remain audited.
- Existing WebSocket clients continue working during migration.

## 4. Non-Goals

- Do not expose hidden chain-of-thought or raw model reasoning tokens.
- Do not redesign the Live orb, Pet skin, Code workbench, or five-system blueprint.
- Do not allow arbitrary process termination when a tool cannot be safely stopped.
- Do not run two mutating agent requests concurrently in one conversation.
- Do not make client-provided `recent_cards` the authoritative memory store.

## 5. Architecture

### 5.1 Backend Conversation Hub

Add a `ConversationHub` owned by `JarvisRuntime`. It maps a stable `session_id` to
one `ConversationSession` containing:

- current request ID and execution task;
- monotonically increasing event sequence;
- attached WebSocket subscribers;
- cancellation state;
- pending approval state;
- durable conversation cursor and latest activity snapshot.

All surfaces attach to the same session. Events are broadcast to every attached
surface, so a request started in Live becomes visible in Code without replaying or
restarting it.

### 5.2 Request Execution

Each accepted request creates one `asyncio.Task`, one cancellation token, and one
durable `AgentRunRecorder`. The hub, rather than a WebSocket handler, owns the
task. Disconnecting or changing surfaces does not silently destroy the request.

Only one request may be active per session. A replacement request performs:

1. mark the previous request as cancellation requested;
2. stop pending LLM streaming and TTS;
3. prevent any not-yet-started tool call from beginning;
4. request cancellation of a safely cancellable active tool;
5. finalize the previous run as `cancelled`;
6. start the replacement request with the same session history.

An irreversible or non-cancellable operation is not killed blindly. The hub emits
`cancellation_pending`, waits for its safe boundary, and then starts the new
request. This preserves the existing permission and action policy.

### 5.3 Stable Conversation Identity

The native app creates or restores one active conversation ID from user data.
Live sends it directly. Code receives it when the iframe is opened and attaches
to that same conversation. Creating a new conversation is an explicit user action
and updates both surfaces.

The backend session store is authoritative. Client cards remain a temporary
compatibility fallback only. User messages, completed assistant messages,
activities, tool references, and interruption markers are persisted by session.
Cancelled partial answers are stored as interrupted output and excluded from
normal model context unless explicitly requested.

## 6. Event Protocol

Every conversation event carries:

- `schema_version`;
- `session_id`;
- `request_id`;
- `sequence`;
- `timestamp`;
- `type`;
- structured payload.

Client commands:

- `conversation.attach` - attach a surface and request replay after a cursor;
- `conversation.message` - submit text with a client-generated idempotency key;
- `conversation.voice` - submit transcribed or captured voice input;
- `conversation.cancel` - cancel the active request;
- `conversation.confirm` - resolve the matching approval;
- `conversation.new` - create a new explicit conversation.

Server activity events:

- `request.accepted`;
- `activity.understanding`;
- `activity.planning`;
- `activity.tool_started`;
- `activity.tool_completed`;
- `activity.verifying`;
- `activity.fallback`;
- `approval.required`;
- `response.delta`;
- `request.cancellation_pending`;
- `request.cancelled`;
- `request.completed`;
- `request.failed`.

The server maps existing `message`, `voice`, `confirm`, `thinking`, `tool_start`,
`tool_result`, `text_delta`, `done`, and `error` messages during migration.
Clients discard stale events whose request ID is no longer active, while still
persisting them for audit.

## 7. Code Experience

Each user turn owns one activity region between the user message and assistant
answer. It begins immediately after submission and remains available after the
turn completes.

The compact view shows one animated state row, elapsed time, and the current
activity. Expanding it reveals an ordered timeline with short user-facing entries:

- Understanding the request
- Planning 3 steps
- Reading 4 files
- Running tests
- Waiting for approval
- Verifying the result
- Completed, failed, or interrupted

Tool inputs and outputs are collapsed by default. Raw terminal output, large JSON,
and model reasoning are never dumped into the activity region. Approval requests
remain explicit and actionable.

The input remains enabled while a request runs. Submitting another message uses
cancel-and-replace. A dedicated stop icon cancels without submitting a new turn.
Completed activity regions collapse automatically but can be reopened.

## 8. Live and Pet Experience

Live keeps the existing orb rendering. It translates the shared activity state to
the current orb states and one concise status line, for example:

- Listening
- Understanding
- Using screen perception
- Running a task
- Waiting for approval
- Verifying
- Interrupted

Detailed tool data remains hidden. Tapping the status may open Code or the task
details surface, but the compact Live window does not expand into a process log.

Voice barge-in has priority over speaking, thinking, and safe tool execution. It
stops TTS immediately, captures the new utterance, cancels the old request, and
submits the transcript as the next turn in the same conversation. Pet consumes
the same state and uses the same short text.

## 9. Tool Failure and Fallback

A failed integration is an execution event, not the end of the conversation. For
example, if a remote note service is unavailable, the agent receives a structured
failure and may select an allowed local notes or file-writing capability. Each
fallback is shown in Code as a short activity and remains subject to permissions.

Fallbacks are bounded by attempt count, risk, and capability policy. Javis must
not claim a note was saved unless the chosen tool returns a verified success.

## 10. Reconnection and Ordering

On reconnect, a surface attaches with its last sequence number. The hub returns a
snapshot and missing events. Duplicate request idempotency keys do not start a
second run. Events are ordered by the session sequence, not client arrival time.

If the app restarts during a request, the durable run is marked interrupted. The
conversation remains available, and the user may retry or resume only when the
stored checkpoint is safe.

## 11. Security and Audit

- Cancellation never bypasses `ToolRegistry`, `ToolGuard`, approval, or action
  policy.
- Approval responses must match session, request, and approval IDs.
- Clients cannot cancel or observe another session without the local session
  capability.
- Every request lifecycle maps to the durable run graph.
- Sensitive tool inputs and secrets are redacted from activity summaries.
- System-critical deletion remains denied; application self-modification and
  deletion retain explicit confirmation requirements.

## 12. Migration

1. Introduce the hub and protocol behind compatibility adapters.
2. Add cancellation and event replay tests before changing clients.
3. Move Live `BackendClient` to the shared session contract.
4. Update the embedded Code client to attach to the native session ID.
5. Replace the transient thinking card with the durable activity timeline.
6. Add voice barge-in and TTS interruption.
7. Remove client cards as the primary history source after compatibility tests.

No memory, blueprint, model, training output, or current Live visual asset is
deleted or rewritten by this migration.

## 13. Verification

Automated verification must cover:

- one session observed simultaneously from Live and Code;
- a Code request appearing in Live state and a Live request appearing in Code;
- cancellation before LLM response, during streaming, before a tool, during a
  cancellable tool, and at a non-cancellable safe boundary;
- new voice input interrupting TTS and continuing the same conversation;
- event ordering, replay, deduplication, and reconnect;
- durable completed, failed, and cancelled run graphs;
- assistant responses persisted in continuous history;
- stale deltas never appearing in the replacement answer;
- legacy WebSocket contract compatibility;
- permission and approval isolation;
- unchanged Live/Pet visual source hashes;
- Python, app, Vite, Cargo, backend startup, packaged sidecar, installer, and
  installed-app smoke tests.

## 14. Acceptance Criteria

- The user can send a new Code or Live instruction while Javis is working.
- The old request visibly becomes interrupted and cannot append late output.
- The new instruction begins without creating a separate conversation.
- Code shows a clear, collapsible activity timeline and a stop control.
- Live and Pet show only the current concise activity and preserve existing looks.
- Opening Code is immediate and shows the current Live task without model work.
- Switching surfaces preserves conversation context and approval state.
- A reconnecting surface catches up without duplicate execution.
- Tool failures can trigger verified, policy-compliant fallback behavior.
- Full source and packaged-app global verification passes before release.

## 15. Source Relationships

The design adapts useful patterns identified in the comparative agent analysis:

- Convex Agent: delta streaming and explicit tool approval lifecycle;
- AgentGPT: run, conclude, next, and error lifecycle separation;
- AgentScope: typed event and middleware boundaries;
- agent-skills: governed capability metadata;
- Javis A1-A4: typed events, progressive tools, governed skills, and durable runs.

Only architecture patterns are reused where licenses or source provenance do not
permit direct copying. The current imported MIT skill set remains candidate-only.
