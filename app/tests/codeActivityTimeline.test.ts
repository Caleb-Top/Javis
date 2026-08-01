import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

type ActivityApi = {
  initialState(requestId: string, now?: number): Record<string, unknown>;
  reduce(state: Record<string, unknown>, event: Record<string, unknown>): Record<string, unknown>;
};

function loadActivityApi(): ActivityApi {
  const source = readFileSync(
    new URL("../../web/js/conversationActivity.js", import.meta.url),
    "utf8",
  );
  const context: Record<string, unknown> = {};
  vm.createContext(context);
  vm.runInContext(source, context);
  return context.JavisConversationActivity as ActivityApi;
}

function event(type: string, requestId: string, sequence: number, payload = {}): Record<string, unknown> {
  return {
    type,
    request_id: requestId,
    sequence,
    timestamp: sequence,
    payload,
  };
}

test("completed activity remains collapsible instead of disappearing", () => {
  const api = loadActivityApi();
  let state = api.initialState("r1", 0);
  state = api.reduce(state, event("activity.planning", "r1", 2, { detail: "Planning 3 steps" }));
  state = api.reduce(state, event("request.completed", "r1", 3));

  assert.equal(state.terminal, "completed");
  assert.equal(state.collapsed, true);
  assert.equal((state.items as unknown[]).length, 1);
});

test("activity summaries never expose private reasoning or raw tool payloads", () => {
  const api = loadActivityApi();
  let state = api.initialState("r1", 0);
  state = api.reduce(state, event("activity.planning", "r1", 1, {
    detail: "Plan the task",
    reasoning_content: "private chain of thought",
    params: { api_key: "secret-value" },
    data: "raw terminal output",
  }));
  const serialized = JSON.stringify(state);

  assert.match(serialized, /Plan the task/);
  assert.doesNotMatch(serialized, /private chain of thought|secret-value|raw terminal output/);
});

test("stale request events cannot mutate the active activity card", () => {
  const api = loadActivityApi();
  const state = api.initialState("r2", 0);
  const next = api.reduce(state, event("activity.tool_started", "r1", 4, { tool: "delete_file" }));

  assert.deepEqual(next, state);
});
