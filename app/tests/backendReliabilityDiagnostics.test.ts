import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import type { BackendReliabilitySnapshot } from "../src/bridge/backendClient.ts";
import {
  summarizeBackendReliability,
  summarizeDiagnosticChecks,
  summarizeVoiceRuntime,
} from "../src/panels/reliabilityDiagnostics.ts";


function snapshot(
  patch: Partial<BackendReliabilitySnapshot> = {},
): BackendReliabilitySnapshot {
  return {
    schemaVersion: 1,
    revision: 4,
    processStartedAt: 100,
    updatedAt: 200,
    timeoutCounts: { accepted: 0, "first-response": 0, overall: 0 },
    lastTimeoutPhase: null,
    requestPhase: "terminal",
    connectionPhase: "connected",
    connectionCount: 1,
    disconnectCount: 0,
    connectionErrorCount: 0,
    reconnectCount: 0,
    recoveryCount: 0,
    reconnectStreak: 0,
    ...patch,
  };
}

test("healthy backend reliability renders as a concise passing check", () => {
  const summary = summarizeBackendReliability(snapshot());

  assert.equal(summary.status, "pass");
  assert.equal(summary.label, "会话响应保护");
  assert.match(summary.message, /ACK 0/);
  assert.match(summary.message, /首字 0/);
  assert.match(summary.message, /总时限 0/);
});

test("timeouts and reconnects are visible without request or conversation data", () => {
  const summary = summarizeBackendReliability(snapshot({
    timeoutCounts: { accepted: 1, "first-response": 2, overall: 3 },
    lastTimeoutPhase: "overall",
    reconnectCount: 4,
    recoveryCount: 3,
  }));

  assert.equal(summary.status, "warn");
  assert.match(summary.message, /ACK 1/);
  assert.match(summary.message, /首字 2/);
  assert.match(summary.message, /总时限 3/);
  assert.match(summary.message, /重连 4/);
  assert.match(summary.message, /恢复 3/);
  for (const forbidden of ["session", "request_id", "text", "audio", "api_key"]) {
    assert.equal(JSON.stringify(summary).toLowerCase().includes(forbidden), false);
  }
});

test("an active connection error is a failing diagnostic", () => {
  const summary = summarizeBackendReliability(snapshot({
    connectionPhase: "error",
    connectionErrorCount: 1,
  }));

  assert.equal(summary.status, "fail");
});

test("merged diagnostic cards determine the final summary instead of a stale backend summary", () => {
  const summary = summarizeDiagnosticChecks([
    { status: "pass" },
    { status: "pass" },
    { status: "warn" },
    { status: "fail" },
  ]);

  assert.deepEqual(summary.counts, { pass: 2, warn: 1, fail: 1 });
  assert.equal(summary.overall, "fail");
  assert.equal(summary.text, "2 项通过，1 项需留意，1 项失败");
  assert.equal(summary.text.includes("0 项失败"), false);
});

test("voice runtime counters expose owner generation, queues, and gateway tasks", () => {
  const summary = summarizeVoiceRuntime({
    continuous: {
      running: true,
      session_attached: true,
      owner_generation: 7,
      owner_identity: "redacted-owner",
      counters: {
        frames: 120,
        turns: 3,
        transcript_final: 2,
        transcript_empty: 1,
        audio_error: 0,
      },
      queues: {
        capture: { current: 0, peak: 2, limit: 96, dropped_frames: 0 },
        transcription: { current: 0, peak: 1, limit: 3, dropped: 0 },
      },
    },
    gateway: {
      active_tasks: 3,
      reconnect_attempts_total: 2,
      recoveries_total: 2,
      errors: { recoverable_total: 1, nonrecoverable_total: 0 },
    },
  });

  assert.equal(summary.status, "pass");
  assert.match(summary.message, /Owner 7/);
  assert.match(summary.message, /任务 3/);
  assert.match(summary.message, /帧 120/);
  assert.match(summary.message, /转写 2\/1\/0/);
  assert.match(summary.message, /队列 0\/2/);
  assert.equal(summary.message.includes("redacted-owner"), false);
});

test("voice drops and terminal errors are visible as warnings without raw payloads", () => {
  const summary = summarizeVoiceRuntime({
    continuous: {
      running: false,
      session_attached: false,
      owner_generation: 8,
      owner_identity: "must-not-render",
      counters: {
        frames: 0,
        turns: 1,
        transcript_final: 0,
        transcript_empty: 0,
        audio_error: 1,
      },
      queues: {
        capture: { current: 0, peak: 4, limit: 96, dropped_frames: 2 },
        transcription: { current: 0, peak: 3, limit: 3, dropped: 1 },
      },
    },
    gateway: {
      active_tasks: 0,
      reconnect_attempts_total: 1,
      recoveries_total: 0,
      errors: { recoverable_total: 1, nonrecoverable_total: 1 },
    },
  });

  assert.equal(summary.status, "warn");
  assert.match(summary.message, /丢帧 2/);
  assert.match(summary.message, /转写丢弃 1/);
  assert.equal(summary.message.includes("must-not-render"), false);
});

test("the app logs the redacted snapshot and diagnostics reads the same client state", () => {
  const main = readFileSync(new URL("../src/main.ts", import.meta.url), "utf8");
  const diagnostics = readFileSync(
    new URL("../src/panels/DiagnosticsPanel.ts", import.meta.url),
    "utf8",
  );

  assert.match(main, /client\.subscribeReliability\(\(snapshot\)\s*=>/);
  assert.match(main, /AppLogger\.write\("info", "backend-reliability", JSON\.stringify\(snapshot\)\)/);
  assert.match(diagnostics, /summarizeBackendReliability\(client\.reliabilitySnapshot\(\)\)/);
  assert.match(diagnostics, /summarizeDiagnosticChecks\(checks\)/);
  assert.doesNotMatch(diagnostics, /report\?\.summary/);
});
