import assert from "node:assert/strict";
import test from "node:test";

import {
  ConversationEventReducer,
  isConversationEvent,
  type ConversationEvent,
} from "../src/conversation/ConversationEventReducer.ts";

function event(
  type: string,
  requestId: string,
  sequence: number,
  payload: Record<string, unknown> = {},
): ConversationEvent {
  return {
    schema_version: 2,
    session_id: "session-1",
    request_id: requestId,
    sequence,
    timestamp: "2026-08-01T00:00:00Z",
    type,
    payload,
  };
}

test("late deltas from an interrupted request are ignored", () => {
  const reducer = new ConversationEventReducer("session-1");
  reducer.accept(event("request.accepted", "r1", 1));
  reducer.accept(event("response.delta", "r1", 2, { text: "old" }));
  reducer.accept(event("request.accepted", "r2", 3));

  const snapshot = reducer.accept(event("response.delta", "r1", 4, { text: " late" }));

  assert.equal(snapshot.response, "");
  assert.equal(snapshot.activeRequestId, "r2");
  assert.equal(snapshot.lastSequence, 4);
});

test("matching deltas form one response and terminal events seal it", () => {
  const reducer = new ConversationEventReducer("session-1");
  reducer.accept(event("request.accepted", "r1", 1));
  reducer.accept(event("response.delta", "r1", 2, { text: "hello" }));
  reducer.accept(event("response.delta", "r1", 3, { text: " Eric" }));

  const snapshot = reducer.accept(event("request.completed", "r1", 4));

  assert.equal(snapshot.response, "hello Eric");
  assert.equal(snapshot.terminal, "completed");
  assert.equal(snapshot.activeRequestId, null);
});

test("duplicates and events for another session do not mutate state", () => {
  const reducer = new ConversationEventReducer("session-1");
  reducer.accept(event("request.accepted", "r1", 5));
  const duplicate = reducer.accept(event("response.delta", "r1", 5, { text: "duplicate" }));
  const foreign = reducer.accept({
    ...event("response.delta", "r1", 6, { text: "foreign" }),
    session_id: "session-2",
  });

  assert.equal(duplicate.response, "");
  assert.equal(foreign.response, "");
  assert.equal(foreign.lastSequence, 5);
});

test("only canonical sequenced events enter the reducer", () => {
  assert.equal(isConversationEvent(event("request.accepted", "r1", 1)), true);
  assert.equal(isConversationEvent({ type: "conversation.attached" }), false);
  assert.equal(isConversationEvent({ ...event("request.accepted", "r1", 1), sequence: "1" }), false);
});
